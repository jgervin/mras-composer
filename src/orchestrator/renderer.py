import asyncio
import logging
import os
import uuid

from src.orchestrator.model import Round
from src.selector.selector import select, select_variants

logger = logging.getLogger(__name__)

_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")


class Renderer:
    """Compose the clip URL(s) for one (owner, round). Deps injected for tests;
    main.py wires them to the real compose/synthesize/url helpers."""

    def __init__(self, db, http, compose, url_for, synthesize):
        self._db = db
        self._http = http
        self._compose = compose          # (selection, audio_path, trigger_id, variant_id) -> Path
        self._url_for = url_for          # Path -> str
        self._synthesize = synthesize    # (text, uuid, voice_id, http) -> Path | None

    async def render(self, owner: str, rnd: Round) -> tuple:
        trigger = {"uuid": owner, "is_new_visitor": False}
        if rnd == Round.OPENER:
            selections = [await select(trigger, self._db)]
        else:
            selections = await select_variants(trigger, self._db, 2)
        audio = await self._synthesize(
            selections[0].tts_text, selections[0].person_uuid, _VOICE_ID, self._http)
        # Per-flow trigger_id: a fresh uuid identifies THIS composition (ad_run) and
        # is threaded to the playback events its clips produce. It must be a real
        # per-trigger id — NOT the owner/person uuid — so the God View's UNIQUE
        # trigger_id constraints don't collide when the same person triggers twice.
        trigger_id = str(uuid.uuid4())
        variant_base = f"orch-{owner}-{int(rnd)}"  # human-readable clip filename base
        # Render each variant independently: one variant's failure must not sink the
        # other (resilience). A failed slot resolves to None — the runtime treats that
        # as an idle/standard fallback for just that display, never dropping both.
        results = await asyncio.gather(*[
            self._compose(sel, audio, trigger_id, f"{variant_base}-{i}")
            for i, sel in enumerate(selections)
        ], return_exceptions=True)
        urls = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning("variant render failed (%s slot %d): %s", trigger_id, i, res)
                urls.append(None)
            else:
                urls.append(self._url_for(res))
        return trigger_id, urls
