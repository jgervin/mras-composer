from unittest.mock import AsyncMock, Mock
import pytest

from src.orchestrator.runtime import OrchestratorRuntime
from src.orchestrator.commands import Idle, Play, RenderAhead
from src.orchestrator.model import Round


def _runtime(render=None):
    return OrchestratorRuntime(
        render=render or AsyncMock(return_value=["urlA", "urlB"]),
        send_play=AsyncMock(),
        send_idle=AsyncMock(),
        arm_watchdog=Mock(),
        cancel_watchdog=Mock(),
    )


async def test_render_ahead_populates_cache_without_sending():
    rt = _runtime(render=AsyncMock(return_value=["a", "b"]))
    await rt.apply([RenderAhead("jason", Round.ROUND2)])
    # the render task is in-flight; await it to settle
    await rt.drain()
    assert rt._cache[("jason", Round.ROUND2)] == ["a", "b"]
    rt._send_play.assert_not_awaited()


async def test_idle_sends_idle_and_cancels_watchdog():
    rt = _runtime()
    await rt.apply([Idle("display-1")])
    rt._send_idle.assert_awaited_once_with("display-1")
    rt._cancel_watchdog.assert_called_once_with("display-1")


async def test_play_with_cached_render_sends_play_and_arms_watchdog():
    rt = _runtime()
    rt._cache[("jason", Round.ROUND2)] = ["urlA", "urlB"]
    await rt.apply([Play("display-3", "jason", Round.ROUND2, 1)])  # slot 1 → urlB
    rt._send_play.assert_awaited_once_with("display-3", "urlB", "jason", Round.ROUND2)
    rt._arm_watchdog.assert_called_once_with("display-3")
    rt._send_idle.assert_not_awaited()


async def test_play_opener_uses_single_cached_url_regardless_of_slot():
    rt = _runtime()
    rt._cache[("jason", Round.OPENER)] = ["opener_url"]
    await rt.apply([Play("display-1", "jason", Round.OPENER, 0)])
    rt._send_play.assert_awaited_once_with("display-1", "opener_url", "jason", Round.OPENER)
