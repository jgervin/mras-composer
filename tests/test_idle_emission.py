"""E6: every idle segment is an observable playback. When the runtime idles a
display it mints a fresh uuid4 trigger_id and emits playback/dispatched with a
null subject and personalization_type='none' so the projector gets an ad_run +
playback row per idle segment."""
import uuid
from unittest.mock import AsyncMock, Mock

from src.orchestrator.commands import Idle, Play
from src.orchestrator.model import Round
from src.orchestrator.runtime import OrchestratorRuntime


def _runtime(emit):
    return OrchestratorRuntime(
        render=AsyncMock(return_value=("tid", ["a"])),
        send_play=AsyncMock(), send_idle=AsyncMock(),
        arm_watchdog=Mock(), cancel_watchdog=Mock(), emit=emit,
    )


async def test_idle_emits_playback_dispatched_with_uuid4_trigger():
    emit = AsyncMock()
    rt = _runtime(emit)
    await rt.apply([Idle("display-1")])

    emit.assert_awaited_once()
    trigger_id, event_type, status, payload = emit.await_args.args
    assert (event_type, status) == ("playback", "dispatched")
    # a real uuid4, never "orch-..." / a person uuid
    assert uuid.UUID(trigger_id).version == 4
    assert payload["trigger_id"] == trigger_id
    assert payload["screen_id"] == "display-1"
    assert payload["screen_kind"] == "display"
    assert payload["subject_profile_id"] is None
    assert payload["personalization_type"] == "none"
    for f in ("used_spoken_name", "used_visible_name", "used_likeness", "used_voice_clone"):
        assert payload[f] is False


async def test_non_idle_commands_do_not_emit():
    emit = AsyncMock()
    rt = _runtime(emit)
    rt._cache[("sp1", Round.OPENER)] = ("tid", ["urlA"])
    await rt.apply([Play("display-1", "sp1", Round.OPENER, 0)])
    emit.assert_not_awaited()


async def test_emit_defaults_to_noop_when_not_injected():
    # Existing wiring that constructs the runtime without an emit dep must not break.
    rt = OrchestratorRuntime(
        render=AsyncMock(), send_play=AsyncMock(), send_idle=AsyncMock(),
        arm_watchdog=Mock(), cancel_watchdog=Mock(),
    )
    await rt.apply([Idle("display-1")])  # must not raise
