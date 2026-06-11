"""Per-display fan-out: with screen_id-tagged kiosk clients connected, an
identified trigger composes one variant per assigned display IN PARALLEL and
sends each display its own clip; with no tagged clients the legacy single-
variant broadcast path is untouched (covered by test_trigger_overlay.py)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import main
from src.selector.selector import AdSelection


def _sel(slug):
    return AdSelection(
        type="personalized", base_video=Path("/assets/standard.mp4"),
        tts_text="Welcome, Jason!", person_uuid="u1",
        composition_id=f"comp-{slug}", overlay_props={"text": "Jason"},
    )


def _client(selections, screen_ids, assigned=None):
    db = AsyncMock()
    db.execute = AsyncMock()
    db.close = AsyncMock()

    async def fake_assemble(base, inserts, trigger_id, overlay_inserts=None):
        return Path(f"/output/{trigger_id}.mp4")

    assemble = AsyncMock(side_effect=fake_assemble)
    synth = AsyncMock(return_value=Path("/tmp/audio.aiff"))
    build_custom = AsyncMock(return_value=[(Path("/tmp/ov.mov"), 0, 2000)])

    patches = [
        patch("main.create_pool", AsyncMock(return_value=db)),
        patch("main.select_variants", AsyncMock(return_value=selections)),
        patch("main.synthesize", synth),
        patch("main.assemble", assemble),
        patch("main.build_custom_overlay_inserts", build_custom),
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
    assigner.assign = MagicMock(return_value=screen_ids if assigned is None else assigned)
    main.app.state.assigner = assigner

    return client, {"assemble": assemble, "ws": ws, "assigner": assigner,
                    "patches": patches}


def _stop(client, mocks):
    client.__exit__(None, None, None)
    for p in mocks["patches"]:
        p.stop()


def test_four_displays_get_four_distinct_clips():
    screens = ["display-1", "display-2", "display-3", "display-4"]
    sels = [_sel(s) for s in ("a", "b", "c", "d")]
    client, mocks = _client(sels, screens)
    try:
        res = client.post("/trigger", json={
            "trigger_id": "t1", "uuid": "u1", "is_new_visitor": False,
            "faces_in_frame": 1,
        })
        assert res.json()["status"] == "ok"
        assert mocks["assemble"].await_count == 4
        assert mocks["ws"].send_to.await_count == 4
        targets = [c.args[0] for c in mocks["ws"].send_to.await_args_list]
        urls = {c.args[1]["video_url"] for c in mocks["ws"].send_to.await_args_list}
        assert targets == screens
        assert len(urls) == 4          # every display got a DIFFERENT clip
        mocks["ws"].broadcast.assert_not_awaited()
    finally:
        _stop(client, mocks)


def test_faces_in_frame_reaches_the_assigner():
    client, mocks = _client([_sel("a"), _sel("b")], ["display-1", "display-2"])
    try:
        client.post("/trigger", json={
            "trigger_id": "t1", "uuid": "u1", "is_new_visitor": False,
            "faces_in_frame": 2,
        })
        args, kwargs = mocks["assigner"].assign.call_args
        passed = list(args) + list(kwargs.values())
        assert 2 in passed
    finally:
        _stop(client, mocks)


def test_all_displays_busy_returns_no_display_without_composing():
    client, mocks = _client([_sel("a")], ["display-1"], assigned=[])
    try:
        res = client.post("/trigger", json={
            "trigger_id": "t1", "uuid": "u1", "is_new_visitor": False,
        })
        assert res.json()["status"] == "no_display"
        mocks["assemble"].assert_not_awaited()
    finally:
        _stop(client, mocks)


def test_one_failed_variant_does_not_sink_the_others():
    screens = ["display-1", "display-2"]
    sels = [_sel("a"), _sel("b")]
    client, mocks = _client(sels, screens)

    calls = {"n": 0}

    async def flaky_assemble(base, inserts, trigger_id, overlay_inserts=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ffmpeg exploded")
        return Path(f"/output/{trigger_id}.mp4")

    mocks["assemble"].side_effect = flaky_assemble
    try:
        res = client.post("/trigger", json={
            "trigger_id": "t1", "uuid": "u1", "is_new_visitor": False,
        })
        assert res.json()["status"] == "ok"
        assert mocks["ws"].send_to.await_count == 1  # surviving variant shipped
    finally:
        _stop(client, mocks)


def test_standard_selection_releases_the_reserved_displays():
    """A new visitor must not freeze the display wall: reservations made at
    assign-time are released when no personalized ad will play."""
    std = AdSelection(type="standard", base_video=Path("/assets/standard.mp4"))
    client, mocks = _client([std], ["display-1", "display-2"])
    try:
        res = client.post("/trigger", json={"trigger_id": "t1", "is_new_visitor": True})
        assert res.json()["status"] == "standard"
        mocks["assigner"].release.assert_called_once_with(["display-1", "display-2"])
    finally:
        _stop(client, mocks)
