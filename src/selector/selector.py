import json
import os
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


async def select(trigger: dict, db) -> AdSelection:
    std = AdSelection(type="standard", base_video=_STANDARD_VIDEO)
    person_uuid = trigger.get("uuid")

    if not person_uuid or trigger.get("is_new_visitor", True):
        return std

    row = await db.fetchrow(
        "SELECT name, is_blocked FROM identities WHERE uuid = $1", person_uuid
    )
    if row is None or row["is_blocked"]:
        return std

    tts_text = _TTS_TEMPLATE.format(name=row["name"])

    ad = await db.fetchrow(
        "SELECT a.base_video, c.slug, a.default_props, a.personalized_field "
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
        "SELECT a.base_video, c.slug, a.default_props, a.personalized_field "
        "FROM ads a JOIN components c ON c.id = a.component_id "
        "WHERE a.is_active = true AND c.status = 'ready' "
        # random per trigger: newest-first starved text-bearing ads on 2-display splits
        "ORDER BY random() LIMIT $1",
        count,
    )
    if not rows:
        return [base] * count

    identity = await db.fetchrow(
        "SELECT name, is_blocked FROM identities WHERE uuid = $1", trigger["uuid"]
    )
    if identity is None:
        # Identity vanished between select() and here — degrade, don't 500.
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
        ))
    return [variants[i % len(variants)] for i in range(count)]
