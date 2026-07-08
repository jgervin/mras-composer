import json
import os
import uuid as uuidlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.selector.perception import (
    Signals, ad_targeting, decision_factors, extract_signals, rank, score_ad,
)

_STANDARD_VIDEO = Path(os.getenv("STANDARD_VIDEO_PATH", "/assets/standard.mp4"))
_TTS_TEMPLATE = os.getenv("TTS_TEMPLATE", "Welcome, {name}!")
_OVERLAY_TEMPLATE = os.getenv("OVERLAY_TEMPLATE", "{name}")


@dataclass
class AdSelection:
    type: Literal["standard", "personalized"]
    base_video: Path
    tts_text: str | None = None
    person_uuid: str | None = None
    # On-screen animated text for the live overlay (None for standard ads).
    overlay_text: str | None = None
    # Custom-component ad fields (M4): set when an active bound component ad is found.
    composition_id: str | None = None
    overlay_props: dict | None = None
    # Identified person's display name (kiosk debug badge; None for standard).
    person_name: str | None = None
    # Row UUIDs for God View event emission (ads.id / components.id). None when
    # no bound custom ad was found (text-overlay fallback / standard).
    ad_id: str | None = None
    component_id: str | None = None
    # ads.personalized_field (TODO-9): the props key the selector overwrote with the
    # person's name. Non-empty ⇒ the bound component itself renders that name, so the
    # always-on text overlay in main._render_overlay_inserts must be skipped. A future
    # non-name-rendering component's ad row can opt back into the overlay by leaving
    # this column '' (empty string; the column is NOT NULL so it can't be None from
    # the DB, but the dataclass default here covers selections built without an ad).
    personalized_field: str | None = None
    # Perception match audit trail (TODO-7): {"perception": {mood, objects,
    # match_score}} when scene_context re-ranked the pick; None otherwise.
    # The renderer stamps it into the decision/made event for God View.
    decision_factors: dict | None = None


async def targeting_column_exists(db) -> bool:
    """Deploy-safety probe (TODO-7 I2): does ads.targeting exist on this DB?

    Called ONCE at lifespan startup; the result is threaded into select()/
    select_variants() as `targeting_supported`. When False the selector runs
    the legacy (pre-TODO-7) SELECT, so an unmigrated DB degrades to exactly
    yesterday's behavior — never UndefinedColumn in the hot trigger path."""
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'ads' AND column_name = 'targeting'"
    )
    return row is not None


async def select(trigger: dict, db, targeting_supported: bool = False) -> AdSelection:
    std = AdSelection(type="standard", base_video=_STANDARD_VIDEO)
    person_uuid = trigger.get("uuid")

    if not person_uuid or trigger.get("is_new_visitor", True):
        return std

    try:
        uuidlib.UUID(person_uuid)
    except (ValueError, TypeError):
        # Garbage trigger uuid: degrade to standard instead of letting
        # asyncpg's $1::uuid cast raise mid-playback.
        return std

    # status = 'known': enrollment writes it explicitly; anonymous/merged/
    # deleted profiles must not personalize with a stale (or NULL) name.
    # false AS is_blocked: blocklist deferred to production go-live (see blocklist_entries).
    row = await db.fetchrow(
        "SELECT display_name AS name, false AS is_blocked FROM subject_profiles "
        "WHERE id = $1::uuid AND status = 'known'", person_uuid
    )
    # display_name is nullable even on known rows — a falsy name degrades too.
    if row is None or not row["name"] or row["is_blocked"]:
        return std

    tts_text = _TTS_TEMPLATE.format(name=row["name"])

    if targeting_supported:
        # Perception re-rank (TODO-7): bounded candidate pool, stable sort.
        # With empty signals or NULL targeting everywhere, rank() preserves the
        # incoming created_at DESC order, so the head is the exact row the
        # legacy LIMIT 1 returns. LIMIT 10: >10 active ads could hide an older
        # matching ad; accepted (bounded hot-query cost).
        signals = extract_signals(trigger.get("scene_context") or {})
        rows = await db.fetch(
            "SELECT a.id AS ad_id, c.id AS component_id, a.base_video, c.slug, "
            "a.default_props, a.personalized_field, a.targeting "
            "FROM ads a JOIN components c ON c.id = a.component_id "
            "WHERE a.is_active = true AND c.status = 'ready' "
            "ORDER BY a.created_at DESC LIMIT 10"
        )
        ad = rank(list(rows), signals)[0] if rows else None
    else:
        # Legacy (pre-TODO-7) SELECT: ads.targeting may not exist yet (I2).
        signals = Signals()
        ad = await db.fetchrow(
            "SELECT a.id AS ad_id, c.id AS component_id, a.base_video, c.slug, "
            "a.default_props, a.personalized_field "
            "FROM ads a JOIN components c ON c.id = a.component_id "
            "WHERE a.is_active = true AND c.status = 'ready' ORDER BY a.created_at DESC LIMIT 1"
        )
    if ad is not None:
        raw = ad["default_props"]
        props = dict(json.loads(raw) if isinstance(raw, str) else (raw or {}))
        props[ad["personalized_field"]] = row["name"]
        factors = None
        if targeting_supported:
            factors = decision_factors(signals, score_ad(ad_targeting(ad), signals))
        return AdSelection(
            type="personalized",
            base_video=Path(ad["base_video"]),
            tts_text=tts_text,
            person_uuid=person_uuid,
            composition_id=f"comp-{ad['slug']}",
            overlay_props=props,
            person_name=row["name"],
            ad_id=str(ad["ad_id"]) if ad["ad_id"] is not None else None,
            component_id=str(ad["component_id"]) if ad["component_id"] is not None else None,
            personalized_field=ad["personalized_field"],
            decision_factors=factors,
        )

    return AdSelection(
        type="personalized",
        base_video=_STANDARD_VIDEO,
        tts_text=tts_text,
        person_uuid=person_uuid,
        overlay_text=_OVERLAY_TEMPLATE.format(name=row["name"]),
        person_name=row["name"],
    )


async def select_variants(
    trigger: dict, db, count: int, targeting_supported: bool = False
) -> list[AdSelection]:
    """Per-display ad selection (T-C): up to `count` DISTINCT active custom
    ads for an identified person, cycled when fewer ads exist than displays.
    Standard/blocked/new-visitor short-circuits to the single legacy
    selection; zero active ads falls back to the legacy text-overlay
    selection on every display."""
    base = await select(trigger, db, targeting_supported)
    if base.type == "standard" or count <= 1:
        return [base]

    if targeting_supported:
        rows = await db.fetch(
            "SELECT a.id AS ad_id, c.id AS component_id, a.base_video, c.slug, "
            "a.default_props, a.personalized_field, a.targeting "
            "FROM ads a JOIN components c ON c.id = a.component_id "
            "WHERE a.is_active = true AND c.status = 'ready' "
            # random per trigger: newest-first starved text-bearing ads on 2-display splits
            "ORDER BY random() LIMIT $1",
            count,
        )
        # Perception re-rank (TODO-7): matched ads fill displays first; ties
        # keep the random() order, preserving the anti-starvation fix.
        rows = rank(list(rows), extract_signals(trigger.get("scene_context") or {}))
    else:
        # Legacy (pre-TODO-7) SELECT: ads.targeting may not exist yet (I2).
        rows = await db.fetch(
            "SELECT a.id AS ad_id, c.id AS component_id, a.base_video, c.slug, "
            "a.default_props, a.personalized_field "
            "FROM ads a JOIN components c ON c.id = a.component_id "
            "WHERE a.is_active = true AND c.status = 'ready' "
            # random per trigger: newest-first starved text-bearing ads on 2-display splits
            "ORDER BY random() LIMIT $1",
            count,
        )
    if not rows:
        return [base] * count

    # Same predicate + guard as select(); false AS is_blocked: blocklist
    # deferred to production go-live (see blocklist_entries).
    identity = await db.fetchrow(
        "SELECT display_name AS name, false AS is_blocked FROM subject_profiles "
        "WHERE id = $1::uuid AND status = 'known'", trigger["uuid"]
    )
    if identity is None or not identity["name"]:
        # Identity vanished (or name NULLed) between select() and here —
        # degrade to the base selection, don't 500 or greet "None".
        return [base] * count
    name = identity["name"]
    tts_text = _TTS_TEMPLATE.format(name=name)

    variants = []
    for ad in rows:
        raw = ad["default_props"]
        props = dict(json.loads(raw) if isinstance(raw, str) else (raw or {}))
        props[ad["personalized_field"]] = name
        variants.append(AdSelection(
            type="personalized",
            base_video=Path(ad["base_video"]),
            tts_text=tts_text,
            person_uuid=trigger["uuid"],
            composition_id=f"comp-{ad['slug']}",
            overlay_props=props,
            person_name=name,
            ad_id=str(ad["ad_id"]) if ad["ad_id"] is not None else None,
            component_id=str(ad["component_id"]) if ad["component_id"] is not None else None,
            personalized_field=ad["personalized_field"],
        ))
    return [variants[i % len(variants)] for i in range(count)]
