"""Activation: with screen_id-tagged kiosks connected, /trigger hands an
identified (non-standard) person to the temporal orchestrator instead of the
old one-shot per-display fan-out. The standard/stranger gate still short-
circuits BEFORE orchestration, and the no-screen-id legacy broadcast path is
untouched (covered in depth by test_trigger_overlay.py)."""
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
