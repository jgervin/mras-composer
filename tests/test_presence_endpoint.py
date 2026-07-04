from unittest.mock import AsyncMock, Mock
import pytest
from fastapi.testclient import TestClient

import main as composer_main
from src.orchestrator.commands import Play
from src.orchestrator.model import Round


@pytest.fixture
def client_with_fakes(monkeypatch):
    orch = Mock()
    orch.on_presence = Mock(return_value=[Play("display-1", "jason", Round.OPENER, 0)])
    orch.on_clip_ended = Mock(return_value=[])
    runtime = Mock()
    runtime.apply = AsyncMock()
    composer_main.app.state.orchestrator = orch
    composer_main.app.state.runtime = runtime
    return TestClient(composer_main.app), orch, runtime


def test_presence_endpoint_feeds_orchestrator_and_applies(client_with_fakes):
    client, orch, runtime = client_with_fakes
    resp = client.post("/presence", json={
        "screen_id": "screen_0",
        "present": [{"uuid": "jason", "first_seen": "2026-06-17T00:00:00Z"}],
    })
    assert resp.status_code == 200
    orch.on_presence.assert_called_once_with(["jason"])
    runtime.apply.assert_awaited_once()


def test_presence_accepts_vision_subject_profile_id_shape(client_with_fakes):
    """Contract with vision's presence reporter (post subject-reroute):
    {"screen_id": ..., "present": [{"subject_profile_id": "<uuid>"}]}."""
    client, orch, runtime = client_with_fakes
    subject = "8b1e3f0a-9c2d-4e5f-8a7b-6c5d4e3f2a1b"
    resp = client.post("/presence", json={
        "screen_id": "screen_0",
        "present": [{"subject_profile_id": subject}],
    })
    assert resp.status_code == 200
    assert resp.json()["present"] == 1
    orch.on_presence.assert_called_once_with([subject])
    runtime.apply.assert_awaited_once()


def test_presence_still_accepts_legacy_uuid_key(client_with_fakes):
    client, orch, runtime = client_with_fakes
    resp = client.post("/presence", json={
        "screen_id": "screen_0",
        "present": [{"uuid": "jason"}],
    })
    assert resp.status_code == 200
    orch.on_presence.assert_called_once_with(["jason"])


def test_presence_person_without_any_subject_key_is_422(client_with_fakes):
    """Contract-regression tripwire: a person entry must carry
    subject_profile_id (or legacy uuid) — neither key is a 422."""
    client, orch, _ = client_with_fakes
    resp = client.post("/presence", json={
        "screen_id": "screen_0",
        "present": [{"first_seen": "2026-07-04T00:00:00Z"}],
    })
    assert resp.status_code == 422
    orch.on_presence.assert_not_called()
