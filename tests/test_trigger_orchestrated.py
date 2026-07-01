"""Activation: with screen_id-tagged kiosks connected, /trigger hands an
identified (non-standard) person to the temporal orchestrator instead of the
old one-shot per-display fan-out. The standard/stranger gate still short-
circuits BEFORE orchestration, and the no-screen-id legacy broadcast path is
untouched (covered in depth by test_trigger_overlay.py)."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi.testclient import TestClient

import main
from src.orchestrator.commands import Play
from src.orchestrator.model import Round
from src.selector.selector import AdSelection


def _personalized():
    return AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                       tts_text="Welcome, Jason!", person_uuid="u1")


def _standard():
    return AdSelection(type="standard", base_video=Path("/assets/standard.mp4"))


def _setup(gate, screen_ids=("display-1",)):
    db = AsyncMock()
    db.execute = AsyncMock()
    db.close = AsyncMock()
    patches = [
        patch("main.create_pool", AsyncMock(return_value=db)),
        patch("main.select", AsyncMock(return_value=gate)),
    ]
    for p in patches:
        p.start()
    client = TestClient(main.app)
    client.__enter__()

    ws = MagicMock()
    ws.screen_ids = MagicMock(return_value=list(screen_ids))
    ws.broadcast = AsyncMock()
    ws.send_to = AsyncMock()
    main.app.state.ws = ws

    orch = Mock()
    orch.on_identify = Mock(return_value=[Play("display-1", "u1", Round.OPENER, 0)])
    runtime = Mock()
    runtime.apply = AsyncMock()
    main.app.state.orchestrator = orch
    main.app.state.runtime = runtime
    return client, {"orch": orch, "runtime": runtime, "ws": ws, "db": db, "patches": patches}


def _stop(client, mocks):
    client.__exit__(None, None, None)
    for p in mocks["patches"]:
        p.stop()


def test_identified_person_drives_the_orchestrator():
    client, mocks = _setup(_personalized())
    try:
        res = client.post("/trigger", json={"trigger_id": "t1", "uuid": "u1",
                                            "is_new_visitor": False})
        assert res.json()["status"] == "orchestrated"
        mocks["orch"].on_identify.assert_called_once_with("u1")
        mocks["runtime"].apply.assert_awaited_once()
        # The old one-shot path is gone — no direct broadcast/send from /trigger.
        mocks["ws"].broadcast.assert_not_awaited()
    finally:
        _stop(client, mocks)


def test_standard_gate_short_circuits_before_orchestration():
    client, mocks = _setup(_standard())
    try:
        res = client.post("/trigger", json={"trigger_id": "t1", "is_new_visitor": True})
        assert res.json()["status"] == "standard"
        mocks["orch"].on_identify.assert_not_called()
        mocks["runtime"].apply.assert_not_awaited()
    finally:
        _stop(client, mocks)


def test_orchestrated_play_logs_playback_event():
    """Dispatching an orchestrated play must log a playback/dispatched event
    (video filename + screen_id + person) so the Activity Feed renders the clip
    link AND the gaze x playback attention-outcome join has its playback side.
    The orchestrated runtime replaced the legacy fan-out, which was the only
    emitter of playback events."""
    db = AsyncMock()
    ws = AsyncMock()
    url = "http://host:8002/media/orch-u1-opener-0.mp4"
    trigger_id = "3f2a0c9e-1b2c-4d5e-8f90-abcdef012345"  # per-flow uuid, NOT the person
    with patch("main._log", AsyncMock()) as log:
        asyncio.run(main._dispatch_play(
            db, ws, "display-2", url, "u1", Round.OPENER, trigger_id))

    # The WS play is still sent to the kiosk (unchanged behavior).
    ws.send_to.assert_awaited_once()
    display_arg, msg = ws.send_to.await_args.args
    assert display_arg == "display-2"
    assert msg["type"] == "play" and msg["video_url"] == url

    # ...and a playback/dispatched event is logged with the clip filename.
    log.assert_awaited_once()
    _db, _trigger_id, event_type, status, payload = log.await_args.args
    assert event_type == "playback"
    assert status == "dispatched"
    # The event's trigger_id is the per-flow id, NOT the person/owner uuid; the
    # person is preserved in the payload instead.
    assert _trigger_id == trigger_id
    assert _trigger_id != "u1"
    assert payload["video"] == "orch-u1-opener-0.mp4"
    assert payload["screen_id"] == "display-2"
    assert payload["person"] == "u1"


def test_no_tagged_kiosk_uses_legacy_broadcast_not_orchestrator():
    client, mocks = _setup(_personalized(), screen_ids=())
    with patch("main.synthesize", AsyncMock(return_value=Path("/tmp/a.aiff"))), \
         patch("main.assemble", AsyncMock(return_value=Path("/output/t1.mp4"))), \
         patch("main._render_overlay_inserts", AsyncMock(return_value=None)):
        try:
            res = client.post("/trigger", json={"trigger_id": "t1", "uuid": "u1",
                                                "is_new_visitor": False})
            assert res.json()["status"] == "ok"
            mocks["orch"].on_identify.assert_not_called()
            mocks["ws"].broadcast.assert_awaited_once()
        finally:
            _stop(client, mocks)
