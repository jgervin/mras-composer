import asyncio
import logging
import os
import time
import uuid

from src.events import display_scope, emit, now_iso
from src.orchestrator.model import Round
from src.selector.selector import select, select_variants

logger = logging.getLogger(__name__)

_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")


def _render_mode(sel) -> str:
    """render_mode enum (010_enums.sql): prebuilt|template_overlay|remotion|..."""
    if sel.type == "standard":
        return "prebuilt"
    if sel.composition_id:
        return "remotion"           # custom Remotion component ad
    return "template_overlay"       # text-overlay-only personalization


def _used_flags(sel, audio_present: bool) -> dict:
    used_spoken = audio_present and sel.tts_text is not None
    used_visible = sel.overlay_text is not None or sel.overlay_props is not None
    return {
        "used_spoken_name": bool(used_spoken),
        "used_visible_name": bool(used_visible),
        "used_likeness": False,                      # face-in-ad not implemented
        "used_voice_clone": bool(used_spoken),       # ElevenLabs synth == voice clone
    }


def _personalization_type(flags: dict) -> str:
    """personalization_type enum: none|...|name|...|hybrid|..."""
    spoken, visible = flags["used_spoken_name"], flags["used_visible_name"]
    if spoken and visible:
        return "hybrid"
    if spoken or visible:
        return "name"
    return "none"


def _decision_type(sel) -> str:
    """decision_type enum: identity chosen for a recognized subject; fallback when
    the render-time re-selection degraded to standard."""
    return "identity" if sel.type == "personalized" else "fallback"


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

        # --- God View decision lane ---------------------------------------------
        # One decision/made per selected ad (personalization_decisions is keyed on
        # event_id, so N selections = N decision rows). The render lane is display-
        # agnostic (one render fans to N displays), so screen_id is None here; the
        # display-lane playback event carries the concrete display and lets the
        # projector back-stamp scope onto the row sharing this trigger_id.
        audio_present = audio is not None
        for sel in selections:
            await emit(self._db, trigger_id, "decision", "made", {
                **display_scope(None),
                "trigger_id": trigger_id,
                "decision_type": _decision_type(sel),
                "selected_ad_id": sel.ad_id,
                "selected_creative_id": sel.component_id,
                "target_subject_profile_id": sel.person_uuid,
                "decision_factors": {},
            })

        # --- God View composition + ad_run open (representative = selections[0]) --
        # composition_runs / ad_runs are keyed UNIQUE(trigger_id): one row per render.
        head = selections[0]
        flags = _used_flags(head, audio_present)
        started_at = now_iso()
        await emit(self._db, trigger_id, "composition", "rendering", {
            **display_scope(None), "trigger_id": trigger_id,
            "ad_id": head.ad_id, "component_id": head.component_id,
            "render_mode": _render_mode(head), **flags,
            "variant_count": len(selections), "started_at": started_at,
        })
        await emit(self._db, trigger_id, "ad_run", "planned", {
            **display_scope(None), "trigger_id": trigger_id,
            "ad_id": head.ad_id,
            "target_subject_profile_id": head.person_uuid,
            "personalization_type": _personalization_type(flags), **flags,
        })

        variant_base = f"orch-{owner}-{int(rnd)}"  # human-readable clip filename base
        # Render each variant independently: one variant's failure must not sink the
        # other (resilience). A failed slot resolves to None — the runtime treats that
        # as an idle/standard fallback for just that display, never dropping both.
        t0 = time.monotonic()
        results = await asyncio.gather(*[
            self._compose(sel, audio, trigger_id, f"{variant_base}-{i}")
            for i, sel in enumerate(selections)
        ], return_exceptions=True)
        urls = []
        failed = 0
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning("variant render failed (%s slot %d): %s", trigger_id, i, res)
                urls.append(None)
                failed += 1
            else:
                urls.append(self._url_for(res))

        latency_ms = int((time.monotonic() - t0) * 1000)
        if failed == len(selections):
            await emit(self._db, trigger_id, "composition", "failed", {
                **display_scope(None), "trigger_id": trigger_id,
                "ad_id": head.ad_id, "component_id": head.component_id,
                "render_mode": _render_mode(head), **flags,
                "error_code": "ALL_VARIANTS_FAILED",
                "error_message": "every variant render raised",
                "failed_variant_count": failed, "variant_count": len(selections),
                "ended_at": now_iso(),
            })
        else:
            await emit(self._db, trigger_id, "composition", "rendered", {
                **display_scope(None), "trigger_id": trigger_id,
                "ad_id": head.ad_id, "component_id": head.component_id,
                "render_mode": _render_mode(head), **flags,
                "failed_variant_count": failed, "variant_count": len(selections),
                "latency_ms": latency_ms, "ended_at": now_iso(),
            })
        return trigger_id, urls
