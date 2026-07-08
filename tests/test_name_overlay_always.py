"""Owner rules (2026-06-11 live test): (1) whenever a name is SPOKEN the name
text must also be WRITTEN — on every personalized variant, custom-Remotion or
not; (2) overlay duration defaults to OVERLAY_DURATION_FRACTION x the base
video (0.5 default; the test rig runs 1.0 = full length).

These rules live in main._render_overlay_inserts, which the orchestrated render
path still drives (Renderer.render -> _compose_variant -> _render_overlay_inserts).
Originally these were asserted through the one-shot /trigger fan-out; that path
was replaced by the temporal orchestrator (see test_trigger_orchestrated.py), so
the owner-rule coverage now exercises _render_overlay_inserts directly."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import main
from src.selector.selector import AdSelection


def _meta(duration_ms=8000):
    m = MagicMock()
    m.width, m.height, m.fps, m.duration_ms = 854, 480, 24, duration_ms
    return m


def _sel(slug, name="Ragnar"):
    return AdSelection(
        type="personalized", base_video=Path("/assets/standard.mp4"),
        tts_text=f"Welcome, {name}!", person_uuid="u1",
        composition_id=f"comp-{slug}", overlay_props={"text": name},
        person_name=name,
    )


async def test_custom_component_variant_also_carries_the_name_text_overlay():
    custom_inserts = [(Path("/tmp/custom.mov"), 0, 8000)]
    text_inserts = [(Path("/tmp/name.mov"), 500, 8000)]
    main.app.state.db = AsyncMock()
    main.app.state.http = AsyncMock()
    with patch("main.build_custom_overlay_inserts", AsyncMock(return_value=custom_inserts)), \
         patch("main.build_overlay_inserts_http", AsyncMock(return_value=text_inserts)), \
         patch("main.probe_video", MagicMock(return_value=_meta())):
        inserts = await main._render_overlay_inserts(_sel("fallingsnow"), "t1")
    # component overlay first, name text composited ON TOP (later in the chain)
    assert inserts == custom_inserts + text_inserts


async def test_name_overlay_spec_duration_is_fraction_of_base():
    main.app.state.db = AsyncMock()
    main.app.state.http = AsyncMock()
    build = AsyncMock(return_value=[(Path("/tmp/name.mov"), 500, 4000)])
    with patch("main.build_custom_overlay_inserts", AsyncMock(return_value=[])), \
         patch("main.build_overlay_inserts_http", build), \
         patch("main.probe_video", MagicMock(return_value=_meta(8000))), \
         patch.object(main, "_OVERLAY_FRACTION", 0.5):
        await main._render_overlay_inserts(_sel("fallingsnow"), "t1")
    specs = build.await_args.args[0]
    assert specs[0].duration_ms == 4000  # 0.5 x 8000ms base
    assert specs[0].text == "Ragnar"


def _sel_component_personalized(slug="helloname", field="text", name="Jason"):
    return AdSelection(
        type="personalized", base_video=Path("/assets/standard.mp4"),
        tts_text=f"Welcome, {name}!", person_uuid="u1",
        composition_id=f"comp-{slug}", overlay_props={field: name},
        person_name=name, personalized_field=field,
    )


async def test_component_personalized_selection_yields_exactly_one_name_source():
    """TODO-9: when the bound component itself renders the name (composition_id +
    personalized_field both set), the always-on name overlay must NOT also fire —
    otherwise the viewer's name is composited twice."""
    custom_inserts = [(Path("/tmp/custom.mov"), 0, 8000)]
    main.app.state.db = AsyncMock()
    main.app.state.http = AsyncMock()
    build_text = AsyncMock(return_value=[(Path("/tmp/name.mov"), 500, 8000)])
    with patch("main.build_custom_overlay_inserts", AsyncMock(return_value=custom_inserts)), \
         patch("main.build_overlay_inserts_http", build_text), \
         patch("main.probe_video", MagicMock(return_value=_meta())):
        inserts = await main._render_overlay_inserts(_sel_component_personalized(), "t1")
    assert inserts == custom_inserts     # component insert only — one name source
    build_text.assert_not_awaited()      # the always-on overlay must not fire


async def test_base_video_only_personalized_selection_still_gets_overlay():
    """TODO-9 regression guard: with no bound component at all (composition_id is
    None — the text-overlay fallback path), the always-on name overlay remains the
    ONLY and REQUIRED name source."""
    text_inserts = [(Path("/tmp/name.mov"), 500, 8000)]
    main.app.state.db = AsyncMock()
    main.app.state.http = AsyncMock()
    sel = AdSelection(
        type="personalized", base_video=Path("/assets/standard.mp4"),
        tts_text="Welcome, Jason!", person_uuid="u1",
        overlay_text="Jason", person_name="Jason",
    )
    with patch("main.build_custom_overlay_inserts", AsyncMock()) as build_custom, \
         patch("main.build_overlay_inserts_http", AsyncMock(return_value=text_inserts)), \
         patch("main.probe_video", MagicMock(return_value=_meta())):
        inserts = await main._render_overlay_inserts(sel, "t1")
    build_custom.assert_not_awaited()   # no component bound at all
    assert inserts == text_inserts


async def test_custom_overlay_duration_defaults_to_fraction_of_base():
    rendered = {}

    async def fake_render(client, url, comp_id, props, work):
        rendered.update(props)
        return Path("/tmp/c.mov")

    with patch("main.render_composition_http", AsyncMock(side_effect=fake_render)), \
         patch("main.assert_conformant", MagicMock()), \
         patch.object(main, "_OVERLAY_FRACTION", 0.5):
        await main.build_custom_overlay_inserts(
            None, "http://x", "comp-a", {"text": "Ragnar"},
            Path("/assets/b.mp4"), Path("/tmp"), probe=MagicMock(return_value=_meta(10000)),
        )
    assert rendered["durationMs"] == 5000  # 0.5 x 10000ms base
