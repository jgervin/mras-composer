"""/trigger must read the cross-service key `subject_profile_id` from the trigger
body (vision renamed uuid -> subject_profile_id). When present it is the subject
handed to the orchestrator; legacy `uuid` is still accepted as a fallback."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi.testclient import TestClient

import main
from src.orchestrator.commands import Play
from src.orchestrator.model import Round
from src.selector.selector import AdSelection


def _setup():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.close = AsyncMock()
    gate = AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                       tts_text="Welcome, Jason!", person_uuid="sp1")
    patches = [
        patch("main.create_pool", AsyncMock(return_value=db)),
        patch("main.select", AsyncMock(return_value=gate)),
    ]
    for p in patches:
        p.start()
    client = TestClient(main.app)
    client.__enter__()
    ws = MagicMock()
    ws.screen_ids = MagicMock(return_value=["display-1"])
    ws.send_to = AsyncMock()
    main.app.state.ws = ws
    orch = Mock()
    orch.on_identify = Mock(return_value=[Play("display-1", "sp1", Round.OPENER, 0)])
    runtime = Mock()
    runtime.apply = AsyncMock()
    main.app.state.orchestrator = orch
    main.app.state.runtime = runtime
    return client, {"orch": orch, "runtime": runtime, "patches": patches}


def _stop(client, mocks):
    client.__exit__(None, None, None)
    for p in mocks["patches"]:
        p.stop()


def test_subject_profile_id_is_the_orchestrated_subject():
    client, mocks = _setup()
    try:
        res = client.post("/trigger", json={"trigger_id": "t1",
                                            "subject_profile_id": "sp1",
                                            "is_new_visitor": False})
        assert res.json()["status"] == "orchestrated"
        mocks["orch"].on_identify.assert_called_once_with("sp1")
    finally:
        _stop(client, mocks)


def test_legacy_uuid_still_accepted_when_no_subject_profile_id():
    client, mocks = _setup()
    mocks["orch"].on_identify = Mock(return_value=[Play("display-1", "u9", Round.OPENER, 0)])
    main.app.state.orchestrator = mocks["orch"]
    try:
        res = client.post("/trigger", json={"trigger_id": "t1", "uuid": "u9",
                                            "is_new_visitor": False})
        assert res.json()["status"] == "orchestrated"
        mocks["orch"].on_identify.assert_called_once_with("u9")
    finally:
        _stop(client, mocks)
