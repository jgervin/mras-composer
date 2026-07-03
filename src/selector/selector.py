import json
import os
import uuid as uuidlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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


async def select(trigger: dict, db) -> AdSelection:
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
        )

    return AdSelection(
        type="personalized",
        base_video=_STANDARD_VIDEO,
        tts_text=tts_text,
        person_uuid=person_uuid,
        overlay_text=_OVERLAY_TEMPLATE.format(name=row["name"]),
        person_name=row["name"],
    )


async def select_variants(trigger: dict, db, count: int) -> list[AdSelection]:
    """Per-display ad selection (T-C): up to `count` DISTINCT active custom
    ads for an identified person, cycled when fewer ads exist than displays.
    Standard/blocked/new-visitor short-circuits to the single legacy
    selection; zero active ads falls back to the legacy text-overlay
    selection on every display."""
    base = await select(trigger, db)
    if base.type == "standard" or count <= 1:
        return [base]

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
        ))
    return [variants[i % len(variants)] for i in range(count)]
