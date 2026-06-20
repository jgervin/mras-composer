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
