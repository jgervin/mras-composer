import asyncio
import logging

from src.orchestrator.commands import Idle, Play, RenderAhead
from src.orchestrator.model import Round

logger = logging.getLogger(__name__)


class OrchestratorRuntime:
    """Maps the pure core's commands to real I/O. Injected deps:
      render(owner, round) -> awaitable[(trigger_id, list[str])]  (opener: 1 URL, round2: 2 URLs)
      send_play(display, url, owner, round, trigger_id) -> awaitable
      send_idle(display) -> awaitable
      arm_watchdog(display) / cancel_watchdog(display) -> None

    The render's trigger_id (the per-flow id for that composition) is cached with
    its URLs and threaded to send_play, so every playback dispatched off one render
    shares that render's trigger_id (and never the owner/person uuid).
    """

    def __init__(self, render, send_play, send_idle, arm_watchdog, cancel_watchdog):
        self._render = render
        self._send_play = send_play
        self._send_idle = send_idle
        self._arm_watchdog = arm_watchdog
        self._cancel_watchdog = cancel_watchdog
        self._cache: dict[tuple, list] = {}
        self._inflight: dict[tuple, asyncio.Task] = {}
        self._pending: dict[str, tuple] = {}  # display -> (owner, round, slot)
        # Serialize apply() so concurrent callers (tick vs clip_ended vs trigger)
        # can't interleave their _cache/_pending mutations across awaits.
        self._lock = asyncio.Lock()

    async def apply(self, commands) -> None:
        async with self._lock:
            for c in commands:
                if isinstance(c, RenderAhead):
                    self._ensure_render(c.owner, c.round)
                elif isinstance(c, Idle):
                    self._pending.pop(c.display, None)
                    self._cancel_watchdog(c.display)
                    await self._send_idle(c.display)
                elif isinstance(c, Play):
                    await self._play(c)

    def _ensure_render(self, owner, rnd) -> None:
        key = (owner, rnd)
        if key in self._cache or key in self._inflight:
            return

        async def run():
            try:
                # render returns (trigger_id, urls); cache both together.
                self._cache[key] = await self._render(owner, rnd)
                await self._resume_pending(owner, rnd)
            except Exception:
                # Never let a render failure become an unretrieved task exception.
                # Pending displays were idled + watchdog-armed at miss time, so the
                # watchdog still advances the program despite the failed render.
                logger.exception("render task failed for %s", key)
            finally:
                self._inflight.pop(key, None)

        self._inflight[key] = asyncio.create_task(run())

    async def _play(self, c: Play) -> None:
        entry = self._cache.get((c.owner, c.round))
        if entry is not None:
            trigger_id, urls = entry
            self._pending.pop(c.display, None)
            url = urls[min(c.pair_slot, len(urls) - 1)]
            if url is None:
                # This variant's render failed → don't wedge the display: idle it and
                # arm the watchdog so the program still advances (clip_ended fires).
                await self._send_idle(c.display)
            else:
                await self._send_play(c.display, url, c.owner, c.round, trigger_id)
            self._arm_watchdog(c.display)
        else:
            # render-gap: idle now, resume this display when the render lands. Arm the
            # watchdog on the idle gap too, so a persistently-failing render still
            # advances the program rather than leaving the display stuck idle forever.
            self._pending[c.display] = (c.owner, c.round, c.pair_slot)
            await self._send_idle(c.display)
            self._arm_watchdog(c.display)
            self._ensure_render(c.owner, c.round)

    async def _resume_pending(self, owner, rnd) -> None:
        trigger_id, urls = self._cache[(owner, rnd)]
        for display, (o, r, slot) in list(self._pending.items()):
            if (o, r) == (owner, rnd):
                del self._pending[display]
                url = urls[min(slot, len(urls) - 1)]
                if url is None:
                    await self._send_idle(display)
                else:
                    await self._send_play(display, url, owner, rnd, trigger_id)
                self._arm_watchdog(display)

    async def drain(self) -> None:
        """Test/shutdown helper: await all in-flight render tasks."""
        while self._inflight:
            await asyncio.gather(*list(self._inflight.values()))
