from unittest.mock import AsyncMock, Mock
import pytest

from src.orchestrator.runtime import OrchestratorRuntime
from src.orchestrator.commands import Idle, Play, RenderAhead
from src.orchestrator.core import Orchestrator
from src.orchestrator.model import Round


def _runtime(render=None):
    # render returns (trigger_id, urls); the runtime caches both and threads the
    # trigger_id through to send_play.
    return OrchestratorRuntime(
        render=render or AsyncMock(return_value=("tid-def", ["urlA", "urlB"])),
        send_play=AsyncMock(),
        send_idle=AsyncMock(),
        arm_watchdog=Mock(),
        cancel_watchdog=Mock(),
    )


async def test_render_ahead_populates_cache_without_sending():
    rt = _runtime(render=AsyncMock(return_value=("tid-ra", ["a", "b"])))
    await rt.apply([RenderAhead("jason", Round.ROUND2)])
    # the render task is in-flight; await it to settle
    await rt.drain()
    assert rt._cache[("jason", Round.ROUND2)] == ("tid-ra", ["a", "b"])
    rt._send_play.assert_not_awaited()


async def test_idle_sends_idle_and_cancels_watchdog():
    rt = _runtime()
    await rt.apply([Idle("display-1")])
    rt._send_idle.assert_awaited_once_with("display-1")
    rt._cancel_watchdog.assert_called_once_with("display-1")


async def test_play_with_cached_render_sends_play_and_arms_watchdog():
    rt = _runtime()
    rt._cache[("jason", Round.ROUND2)] = ("tid-1", ["urlA", "urlB"])
    await rt.apply([Play("display-3", "jason", Round.ROUND2, 1)])  # slot 1 → urlB
    rt._send_play.assert_awaited_once_with("display-3", "urlB", "jason", Round.ROUND2, "tid-1")
    rt._arm_watchdog.assert_called_once_with("display-3")
    rt._send_idle.assert_not_awaited()


async def test_play_opener_uses_single_cached_url_regardless_of_slot():
    rt = _runtime()
    rt._cache[("jason", Round.OPENER)] = ("tid-op", ["opener_url"])
    await rt.apply([Play("display-1", "jason", Round.OPENER, 0)])
    rt._send_play.assert_awaited_once_with(
        "display-1", "opener_url", "jason", Round.OPENER, "tid-op")


async def test_play_on_miss_idles_then_resumes_when_render_completes():
    render = AsyncMock(return_value=("tid-miss", ["renderedA", "renderedB"]))
    rt = _runtime(render=render)
    await rt.apply([Play("display-2", "jason", Round.ROUND2, 0)])
    # no cache yet → idle now, render kicked off. The watchdog is armed on the
    # idle gap so a stuck render still advances the program (see render-failure test).
    rt._send_idle.assert_awaited_once_with("display-2")
    rt._send_play.assert_not_awaited()
    rt._arm_watchdog.assert_called_once_with("display-2")
    await rt.drain()  # let the render task finish → it resumes the pending display
    rt._send_play.assert_awaited_once_with(
        "display-2", "renderedA", "jason", Round.ROUND2, "tid-miss")
    # re-armed on the actual play
    assert rt._arm_watchdog.call_count == 2


async def test_failed_variant_slot_idles_and_arms_watchdog_to_advance():
    # A cached render whose slot is None (that variant failed) must NOT wedge the
    # display: idle it and arm the watchdog so the program still advances.
    rt = _runtime()
    rt._cache[("jason", Round.ROUND2)] = ("tid-f", ["urlA", None])
    await rt.apply([Play("display-4", "jason", Round.ROUND2, 1)])  # slot 1 → None
    rt._send_idle.assert_awaited_once_with("display-4")
    rt._send_play.assert_not_awaited()
    rt._arm_watchdog.assert_called_once_with("display-4")


async def test_one_failed_slot_still_plays_the_surviving_slot():
    rt = _runtime()
    rt._cache[("jason", Round.ROUND2)] = ("tid-s", ["urlA", None])
    await rt.apply([Play("display-3", "jason", Round.ROUND2, 0)])  # slot 0 → urlA
    rt._send_play.assert_awaited_once_with("display-3", "urlA", "jason", Round.ROUND2, "tid-s")
    rt._arm_watchdog.assert_called_once_with("display-3")
    rt._send_idle.assert_not_awaited()


async def test_render_task_exception_does_not_raise_and_clears_inflight():
    render = AsyncMock(side_effect=RuntimeError("render blew up"))
    rt = _runtime(render=render)
    await rt.apply([Play("display-2", "jason", Round.ROUND2, 0)])
    # idle + watchdog armed on the miss; the watchdog guarantees forward progress
    rt._send_idle.assert_awaited_once_with("display-2")
    rt._arm_watchdog.assert_called_once_with("display-2")
    await rt.drain()  # render task raises internally but is caught (no unretrieved exc)
    assert rt._inflight == {}
    rt._send_play.assert_not_awaited()


async def test_repeat_visit_after_done_does_not_replay_prior_trigger_id():
    # issue #27: the runtime cache is keyed (owner, round) and Round values repeat
    # across programs, so when a subject's program completes (DONE) and the same
    # person re-triggers later, the core mints a FRESH program but the runtime
    # replays the previous visit's cached render — wrong trigger_id (breaks God
    # View playback↔ad_run linkage) and stale content. The new program's opener
    # must be a fresh render with a fresh trigger_id.
    calls = []

    async def render(owner, rnd, screen_id=None):
        calls.append((owner, rnd))
        n = calls.count((owner, rnd))
        return (f"tid-{rnd.name}-{n}", [f"urlA-{rnd.name}-{n}", f"urlB-{rnd.name}-{n}"])

    core = Orchestrator(["display-1"], clock=lambda: 0.0)
    rt = _runtime(render=render)

    # visit 1: opener (cache-miss → render) → round 2 → DONE
    await rt.apply(core.on_identify("jason"))
    await rt.drain()
    await rt.apply(core.on_clip_ended("display-1"))  # opener ended → round 2
    await rt.drain()
    await rt.apply(core.on_clip_ended("display-1"))  # round 2 ended → DONE → idle
    await rt.drain()

    # visit 2: same person re-triggers → core starts a FRESH program (opener)
    await rt.apply(core.on_identify("jason"))
    await rt.drain()

    # a fresh opener render was requested (cache miss, not a stale-cache replay)
    assert calls.count(("jason", Round.OPENER)) == 2
    # and the dispatched opener carries the fresh render's trigger_id
    opener_dispatches = [args for (args, _kw) in rt._send_play.await_args_list
                         if args[3] == Round.OPENER]
    assert opener_dispatches[-1][4] == "tid-OPENER-2"
