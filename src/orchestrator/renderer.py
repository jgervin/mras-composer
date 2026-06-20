import asyncio
import logging
import os

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

    async def render(self, owner: str, rnd: Round) -> list:
        trigger = {"uuid": owner, "is_new_visitor": False}
        if rnd == Round.OPENER:
            selections = [await select(trigger, self._db)]
        else:
            selections = await select_variants(trigger, self._db, 2)
        audio = await self._synthesize(
            selections[0].tts_text, selections[0].person_uuid, _VOICE_ID, self._http)
        tid = f"orch-{owner}-{int(rnd)}"
        # Render each variant independently: one variant's failure must not sink the
        # other (resilience). A failed slot resolves to None — the runtime treats that
        # as an idle/standard fallback for just that display, never dropping both.
        results = await asyncio.gather(*[
            self._compose(sel, audio, tid, f"{tid}-{i}")
            for i, sel in enumerate(selections)
        ], return_exceptions=True)
        urls = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning("variant render failed (%s slot %d): %s", tid, i, res)
                urls.append(None)
            else:
                urls.append(self._url_for(res))
        return urls
