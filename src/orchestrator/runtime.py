import asyncio

from src.orchestrator.commands import Idle, Play, RenderAhead
from src.orchestrator.model import Round


class OrchestratorRuntime:
    """Maps the pure core's commands to real I/O. Injected deps:
      render(owner, round) -> awaitable[list[str]]  (opener: 1 URL, round2: 2 URLs)
      send_play(display, url, owner, round) -> awaitable
      send_idle(display) -> awaitable
      arm_watchdog(display) / cancel_watchdog(display) -> None
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

    async def apply(self, commands) -> None:
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
                self._cache[key] = await self._render(owner, rnd)
                await self._resume_pending(owner, rnd)
            finally:
                self._inflight.pop(key, None)

        self._inflight[key] = asyncio.create_task(run())

    async def _play(self, c: Play) -> None:
        raise NotImplementedError  # Task 2

    async def _resume_pending(self, owner, rnd) -> None:
        return  # no-op until Task 3 (no pending displays to resume yet)

    async def drain(self) -> None:
        """Test/shutdown helper: await all in-flight render tasks."""
        while self._inflight:
            await asyncio.gather(*list(self._inflight.values()))
