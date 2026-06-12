"""Owner rules (2026-06-11 live test): (1) whenever a name is SPOKEN the name
text must also be WRITTEN — on every personalized variant, custom-Remotion or
not; (2) overlay duration defaults to OVERLAY_DURATION_FRACTION x the base
video (0.5 default; the test rig runs 1.0 = full length); (3) each variant is
sent to its display the moment it is ready, not after all variants finish."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

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


def _client(selections, screen_ids):
    db = AsyncMock()
    db.execute = AsyncMock()
    db.close = AsyncMock()

    async def fake_assemble(base, inserts, trigger_id, overlay_inserts=None):
        return Path(f"/output/{trigger_id}.mp4")

    assemble = AsyncMock(side_effect=fake_assemble)
    custom_inserts = [(Path("/tmp/custom.mov"), 0, 8000)]
    text_inserts = [(Path("/tmp/name.mov"), 500, 8000)]

    patches = [
        patch("main.create_pool", AsyncMock(return_value=db)),
        patch("main.select", AsyncMock(return_value=_sel("gate"))),
        patch("main.select_variants", AsyncMock(return_value=selections)),
        patch("main.synthesize", AsyncMock(return_value=Path("/tmp/audio.aiff"))),
        patch("main.assemble", assemble),
        patch("main.build_custom_overlay_inserts", AsyncMock(return_value=custom_inserts)),
        patch("main.build_overlay_inserts_http", AsyncMock(return_value=text_inserts)),
        patch("main.probe_video", MagicMock(return_value=_meta())),
    ]
    for p in patches:
        p.start()
    client = TestClient(main.app)
    client.__enter__()

    ws = MagicMock()
    ws.screen_ids = MagicMock(return_value=screen_ids)
    ws.send_to = AsyncMock()
    ws.broadcast = AsyncMock()
    main.app.state.ws = ws
    assigner = MagicMock()
    assigner.assign = MagicMock(return_value=screen_ids)
    main.app.state.assigner = assigner

    return client, {"assemble": assemble, "ws": ws,
                    "custom_inserts": custom_inserts, "text_inserts": text_inserts,
                    "patches": patches}


def _stop(client, mocks):
    client.__exit__(None, None, None)
    for p in mocks["patches"]:
        p.stop()


def test_custom_component_variant_also_carries_the_name_text_overlay():
    client, mocks = _client([_sel("fallingsnow")], ["display-1"])
    try:
        res = client.post("/trigger", json={"trigger_id": "t1", "uuid": "u1",
                                            "is_new_visitor": False})
        assert res.json()["status"] == "ok"
        _, kwargs = mocks["assemble"].call_args
        # component overlay first, name text composited ON TOP (later in chain)
        assert kwargs["overlay_inserts"] == mocks["custom_inserts"] + mocks["text_inserts"]
    finally:
        _stop(client, mocks)


def test_name_overlay_spec_duration_is_fraction_of_base():
    client, mocks = _client([_sel("fallingsnow")], ["display-1"])
    try:
        with patch.object(main, "_OVERLAY_FRACTION", 0.5):
            client.post("/trigger", json={"trigger_id": "t1", "uuid": "u1",
                                          "is_new_visitor": False})
        specs = main.build_overlay_inserts_http.await_args.args[0]
        assert specs[0].duration_ms == 4000  # 0.5 x 8000ms base
        assert specs[0].text == "Ragnar"
    finally:
        _stop(client, mocks)


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


def test_each_variant_is_sent_the_moment_it_is_ready():
    order = []
    client, mocks = _client([_sel("a"), _sel("b")], ["display-1", "display-2"])

    async def staggered_assemble(base, inserts, trigger_id, overlay_inserts=None):
        if trigger_id.endswith("-1"):
            await asyncio.sleep(0.2)
            order.append("variant-2-assembled")
        return Path(f"/output/{trigger_id}.mp4")

    mocks["assemble"].side_effect = staggered_assemble
    sent = mocks["ws"].send_to

    async def record_send(screen_id, msg):
        order.append(f"sent-{screen_id}")

    sent.side_effect = record_send
    try:
        client.post("/trigger", json={"trigger_id": "t1", "uuid": "u1",
                                      "is_new_visitor": False})
        # display-1's fast variant must ship BEFORE the slow variant finishes
        assert order.index("sent-display-1") < order.index("variant-2-assembled")
    finally:
        _stop(client, mocks)
