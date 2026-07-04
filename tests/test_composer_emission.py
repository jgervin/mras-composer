"""God View emissions from the render lane (Renderer.render) — decision/made,
composition/queued, composition/rendering, composition/rendered|failed,
ad_run/planned. Enum values cross-checked against mras-ops 010_enums.sql. These
pre-display events carry the TRIGGER's CAMERA screen_id (screen_kind='camera') so
the projector resolves system/location/org from the cameras registry — a display
scope with screen_id=None would leave these rows permanently unscoped.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.orchestrator.model import Round
from src.orchestrator.renderer import Renderer
from src.selector.selector import AdSelection

_DECISION_TYPES = {"identity", "demographic", "contextual", "scheduled",
                   "manual", "fallback", "blocked_suppressed", "error_recovery"}
_RENDER_MODES = {"prebuilt", "template_overlay", "remotion", "ffmpeg",
                 "genai_video", "fallback"}
_PERSONALIZATION_TYPES = {"none", "demographic", "contextual", "identity", "name",
                          "likeness", "hybrid", "fallback", "suppressed"}


def _custom_sel(name="Jason"):
    return AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                       tts_text=f"Welcome, {name}!", person_uuid="sp1",
                       composition_id="comp-neon", overlay_props={"text": name},
                       person_name=name, ad_id="ad-1", component_id="comp-9")


def _renderer(compose, emit):
    r = Renderer(AsyncMock(), AsyncMock(), compose=compose,
                 url_for=lambda p: f"u/{p.name}",
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    return r


def _events(emit):
    """[(event_type, status, payload), ...] from a captured emit AsyncMock."""
    out = []
    for c in emit.await_args_list:
        _db, _tid, et, st, pl = c.args
        out.append((et, st, pl))
    return out


async def test_opener_emits_decision_composition_and_adrun():
    emit = AsyncMock()
    compose = AsyncMock(return_value=Path("/tmp/op.mp4"))
    r = _renderer(compose, emit)
    with patch("src.orchestrator.renderer.emit", emit), \
         patch("src.orchestrator.renderer.select", AsyncMock(return_value=_custom_sel())):
        trigger_id, urls = await r.render("sp1", Round.OPENER, "cam-77")

    evs = _events(emit)
    kinds = [(et, st) for et, st, _ in evs]
    assert ("decision", "made") in kinds
    assert ("composition", "queued") in kinds
    assert ("composition", "rendering") in kinds
    assert ("composition", "rendered") in kinds
    assert ("ad_run", "planned") in kinds

    # composition/queued opens the composition_runs row before rendering advances it.
    assert kinds.index(("composition", "queued")) < kinds.index(("composition", "rendering"))

    # These pre-display events carry the CAMERA scope (the triggering camera
    # screen_id), so the projector can resolve system/location/org — a display
    # scope with screen_id=None would leave these rows permanently unscoped.
    for et, st, pl in evs:
        assert pl["screen_kind"] == "camera"
        assert pl["screen_id"] == "cam-77"
        assert pl["trigger_id"] == trigger_id

    dec = next(pl for et, st, pl in evs if (et, st) == ("decision", "made"))
    assert dec["decision_type"] in _DECISION_TYPES
    assert dec["decision_type"] == "identity"
    assert dec["selected_ad_id"] == "ad-1"
    assert dec["selected_creative_id"] == "comp-9"
    assert dec["target_subject_profile_id"] == "sp1"
    assert "decision_factors" in dec
    assert "status" not in dec  # personalization_decisions has NO status column

    comp = next(pl for et, st, pl in evs if (et, st) == ("composition", "rendered"))
    assert comp["render_mode"] in _RENDER_MODES
    assert comp["render_mode"] == "remotion"
    assert comp["ad_id"] == "ad-1" and comp["component_id"] == "comp-9"
    for f in ("used_spoken_name", "used_visible_name", "used_likeness", "used_voice_clone"):
        assert isinstance(comp[f], bool)
    assert comp["used_spoken_name"] is True
    assert comp["failed_variant_count"] == 0

    adr = next(pl for et, st, pl in evs if (et, st) == ("ad_run", "planned"))
    assert adr["personalization_type"] in _PERSONALIZATION_TYPES
    assert adr["ad_id"] == "ad-1"
    assert adr["target_subject_profile_id"] == "sp1"


async def test_all_variants_failed_emits_composition_failed_not_rendered():
    emit = AsyncMock()
    compose = AsyncMock(side_effect=RuntimeError("boom"))
    r = _renderer(compose, emit)
    with patch("src.orchestrator.renderer.emit", emit), \
         patch("src.orchestrator.renderer.select", AsyncMock(return_value=_custom_sel())):
        await r.render("sp1", Round.OPENER)

    kinds = [(et, st) for et, st, _ in _events(emit)]
    assert ("composition", "failed") in kinds
    assert ("composition", "rendered") not in kinds


async def test_text_fallback_selection_uses_template_overlay_render_mode():
    emit = AsyncMock()
    sel = AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                      tts_text="Welcome, Jason!", person_uuid="sp1",
                      overlay_text="Jason", person_name="Jason")
    r = _renderer(AsyncMock(return_value=Path("/tmp/op.mp4")), emit)
    with patch("src.orchestrator.renderer.emit", emit), \
         patch("src.orchestrator.renderer.select", AsyncMock(return_value=sel)):
        await r.render("sp1", Round.OPENER)
    comp = next(pl for et, st, pl in _events(emit) if (et, st) == ("composition", "rendering"))
    assert comp["render_mode"] == "template_overlay"
