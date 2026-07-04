"""Composer receive+emit side of the display echo lane. When the display echoes
playback_started / playback_ended over its WS (carrying the trigger_id the
composer put in the outbound play), the composer relays them into the events
journal as playback/started|ended (+ ad_run/playing|completed), server clock
authoritative, screen_kind='display'. (The display SENDING side is a separate lane.)
"""
import asyncio
from unittest.mock import AsyncMock, patch

import main


def _calls(log):
    return [(c.args[1], c.args[2], c.args[3], c.args[4]) for c in log.await_args_list]


def test_playback_started_echo_emits_started_and_ad_run_playing():
    db = AsyncMock()
    msg = {"type": "playback_started", "screen_id": "display-1",
           "trigger_id": "3f2a0c9e-1b2c-4d5e-8f90-abcdef012345"}
    with patch("main._log", AsyncMock()) as log:
        handled = asyncio.run(main._handle_display_echo(db, msg))
    assert handled is True
    calls = _calls(log)
    kinds = [(et, st) for _tid, et, st, _pl in calls]
    assert ("playback", "started") in kinds
    assert ("ad_run", "playing") in kinds
    for tid, et, st, pl in calls:
        assert tid == msg["trigger_id"]
        assert pl["screen_kind"] == "display"
        assert pl["screen_id"] == "display-1"
        assert pl["trigger_id"] == msg["trigger_id"]
    started = next(pl for tid, et, st, pl in calls if (et, st) == ("playback", "started"))
    assert started["started_at"]


def test_playback_ended_echo_emits_ended_with_duration_and_ad_run_completed():
    db = AsyncMock()
    msg = {"type": "playback_ended", "screen_id": "display-1",
           "trigger_id": "3f2a0c9e-1b2c-4d5e-8f90-abcdef012345", "duration_ms": 12345}
    with patch("main._log", AsyncMock()) as log:
        handled = asyncio.run(main._handle_display_echo(db, msg))
    assert handled is True
    calls = _calls(log)
    kinds = [(et, st) for _tid, et, st, _pl in calls]
    assert ("playback", "ended") in kinds
    assert ("ad_run", "completed") in kinds
    ended = next(pl for tid, et, st, pl in calls if (et, st) == ("playback", "ended"))
    assert ended["ended_at"]
    assert ended["duration_ms"] == 12345


def test_echo_without_trigger_id_is_ignored():
    db = AsyncMock()
    with patch("main._log", AsyncMock()) as log:
        handled = asyncio.run(main._handle_display_echo(
            db, {"type": "playback_started", "screen_id": "display-1"}))
    assert handled is False
    log.assert_not_awaited()


def test_unknown_message_type_is_ignored():
    db = AsyncMock()
    with patch("main._log", AsyncMock()) as log:
        handled = asyncio.run(main._handle_display_echo(
            db, {"type": "clip_ended", "screen_id": "display-1", "trigger_id": "x"}))
    assert handled is False
    log.assert_not_awaited()


def test_outbound_play_carries_trigger_id_for_echo():
    """The composer must put trigger_id in the outbound play so the display can
    echo it back on started/ended (the handshake half of this lane)."""
    from src.orchestrator.model import Round
    ws = AsyncMock()
    tid = "3f2a0c9e-1b2c-4d5e-8f90-abcdef012345"
    with patch("main._log", AsyncMock()):
        asyncio.run(main._dispatch_play(AsyncMock(), ws, "display-2",
                                        "http://h/media/x.mp4", "sp1", Round.OPENER, tid))
    _display, msg = ws.send_to.await_args.args
    assert msg["type"] == "play"
    assert msg["trigger_id"] == tid
