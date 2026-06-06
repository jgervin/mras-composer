import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_STANDARD_VIDEO = Path(os.getenv("STANDARD_VIDEO_PATH", "/assets/standard.mp4"))
_TTS_TEMPLATE = os.getenv("TTS_TEMPLATE", "Welcome, {name}!")


@dataclass
class AdSelection:
    type: Literal["standard", "personalized"]
    base_video: Path
    tts_text: str | None = None
    person_uuid: str | None = None


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
    return AdSelection(
        type="personalized",
        base_video=_STANDARD_VIDEO,
        tts_text=tts_text,
        person_uuid=person_uuid,
    )
