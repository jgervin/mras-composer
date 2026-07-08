from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.orchestrator.model import Round
from src.orchestrator.renderer import Renderer
from src.selector.selector import AdSelection


def _sel(name="Jason"):
    return AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                       tts_text=f"Welcome, {name}!", person_uuid="jason",
                       overlay_text=name, person_name=name)


async def test_round2_renders_two_variants_in_order():
    db, http = AsyncMock(), AsyncMock()
    compose = AsyncMock(side_effect=[Path("/tmp/x-0.mp4"), Path("/tmp/x-1.mp4")])
    url = lambda p: f"http://c/media/{p.name}"
    r = Renderer(db, http, compose=compose, url_for=url,
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    with patch("src.orchestrator.renderer.select_variants",
               AsyncMock(return_value=[_sel(), _sel()])):
        trigger_id, urls = await r.render("jason", Round.ROUND2)
    assert urls == ["http://c/media/x-0.mp4", "http://c/media/x-1.mp4"]
    assert compose.await_count == 2


async def test_one_failed_variant_does_not_sink_the_others():
    # If one round-2 variant fails to compose, the other still yields a URL and the
    # failing slot resolves to None (idle/standard fallback) — never raises, never
    # drops both displays.
    db, http = AsyncMock(), AsyncMock()
    compose = AsyncMock(side_effect=[Path("/tmp/ok-0.mp4"), RuntimeError("boom")])
    r = Renderer(db, http, compose=compose, url_for=lambda p: f"u/{p.name}",
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    with patch("src.orchestrator.renderer.select_variants",
               AsyncMock(return_value=[_sel(), _sel()])):
        trigger_id, urls = await r.render("jason", Round.ROUND2)
    assert urls == ["u/ok-0.mp4", None]


async def test_opener_renders_single_variant():
    db, http = AsyncMock(), AsyncMock()
    compose = AsyncMock(return_value=Path("/tmp/op.mp4"))
    r = Renderer(db, http, compose=compose, url_for=lambda p: f"u/{p.name}",
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    with patch("src.orchestrator.renderer.select", AsyncMock(return_value=_sel())):
        trigger_id, urls = await r.render("jason", Round.OPENER)
    assert urls == ["u/op.mp4"]
    assert compose.await_count == 1


# ---------------------------------------------------------------------------
# TODO-7: scene_context handoff + decision_factors emission.
# ---------------------------------------------------------------------------

_CTX = {"viewer": {"mood": "sad", "mood_confidence": 0.9}}


async def test_render_threads_scene_context_and_targeting_flag_into_select():
    db, http = AsyncMock(), AsyncMock()
    r = Renderer(db, http, compose=AsyncMock(return_value=Path("/tmp/op.mp4")),
                 url_for=lambda p: f"u/{p.name}",
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")),
                 scene_ctx_for=lambda owner: _CTX,
                 targeting_supported=True)
    sel = AsyncMock(return_value=_sel())
    with patch("src.orchestrator.renderer.select", sel):
        await r.render("jason", Round.OPENER)
    trigger = sel.await_args.args[0]
    assert trigger["scene_context"] is _CTX
    assert trigger["uuid"] == "jason"
    assert sel.await_args.args[2] is True  # targeting_supported threaded


async def test_render_defaults_to_empty_scene_context_and_legacy_variant():
    # Renderers built without the new deps (all existing tests/back-compat)
    # behave exactly as before: empty context, legacy (flag=False) selection.
    db, http = AsyncMock(), AsyncMock()
    r = Renderer(db, http, compose=AsyncMock(return_value=Path("/tmp/op.mp4")),
                 url_for=lambda p: f"u/{p.name}",
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    sel = AsyncMock(return_value=_sel())
    with patch("src.orchestrator.renderer.select", sel):
        await r.render("jason", Round.OPENER)
    assert sel.await_args.args[0]["scene_context"] == {}
    assert sel.await_args.args[2] is False


async def test_decision_made_emits_selection_decision_factors():
    factors = {"perception": {"mood": "sad", "objects": [], "match_score": 2}}
    matched = AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                          tts_text="Welcome, Jason!", person_uuid="jason",
                          person_name="Jason", decision_factors=factors)
    db, http = AsyncMock(), AsyncMock()
    r = Renderer(db, http, compose=AsyncMock(return_value=Path("/tmp/op.mp4")),
                 url_for=lambda p: f"u/{p.name}",
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    emit = AsyncMock()
    with patch("src.orchestrator.renderer.select", AsyncMock(return_value=matched)), \
         patch("src.orchestrator.renderer.emit", emit):
        await r.render("jason", Round.OPENER)
    decision = next(c.args for c in emit.await_args_list
                    if c.args[2] == "decision" and c.args[3] == "made")
    assert decision[4]["decision_factors"] == factors


async def test_decision_made_emits_empty_factors_when_none():
    db, http = AsyncMock(), AsyncMock()
    r = Renderer(db, http, compose=AsyncMock(return_value=Path("/tmp/op.mp4")),
                 url_for=lambda p: f"u/{p.name}",
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    emit = AsyncMock()
    with patch("src.orchestrator.renderer.select", AsyncMock(return_value=_sel())), \
         patch("src.orchestrator.renderer.emit", emit):
        await r.render("jason", Round.OPENER)
    decision = next(c.args for c in emit.await_args_list
                    if c.args[2] == "decision" and c.args[3] == "made")
    assert decision[4]["decision_factors"] == {}
