import asyncio


class Watchdog:
    """Per-display timer that fires on_timeout(display) if not cancelled in time.
    clip_seconds(display) gives the expected clip length; grace_s is slack."""

    def __init__(self, on_timeout, clip_seconds, grace_s: float = 2.0):
        self._on_timeout = on_timeout
        self._clip_seconds = clip_seconds
        self._grace = grace_s
        self._timers: dict[str, asyncio.Task] = {}

    def arm(self, display: str) -> None:
        self.cancel(display)

        async def run():
            try:
                await asyncio.sleep(self._clip_seconds(display) + self._grace)
                await self._on_timeout(display)
            except asyncio.CancelledError:
                pass
            finally:
                self._timers.pop(display, None)

        self._timers[display] = asyncio.create_task(run())

    def cancel(self, display: str) -> None:
        t = self._timers.pop(display, None)
        if t is not None:
            t.cancel()
