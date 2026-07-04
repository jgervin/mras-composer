import asyncio
import logging
import uuid

from src.events import display_scope, now_iso
from src.orchestrator.commands import EvictRender, Idle, Play, RenderAhead
from src.orchestrator.model import Round

logger = logging.getLogger(__name__)


async def _noop_emit(*_args, **_kwargs) -> None:
    return None


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

    def __init__(self, render, send_play, send_idle, arm_watchdog, cancel_watchdog,
                 emit=None):
        self._render = render
        self._send_play = send_play
        self._send_idle = send_idle
        self._arm_watchdog = arm_watchdog
        self._cancel_watchdog = cancel_watchdog
        # emit(trigger_id, event_type, status, payload) -> awaitable. Defaults to a
        # no-op so wiring that predates God View emission keeps working.
        self._emit = emit or _noop_emit
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
                    self._ensure_render(c.owner, c.round, c.screen_id)
                elif isinstance(c, Idle):
                    self._pending.pop(c.display, None)
                    self._cancel_watchdog(c.display)
                    await self._send_idle(c.display)
                    await self._emit_idle(c.display)
                elif isinstance(c, Play):
                    await self._play(c)
                elif isinstance(c, EvictRender):
                    self._evict(c.owner)

    async def _emit_idle(self, display) -> None:
        """E6: an idle segment is an observable playback with a null subject, so it
        emits playback/dispatched (and only that — an idle segment intentionally has
        no ad_run row). Mint a fresh uuid4 trigger_id per segment so the God View's
        UNIQUE(trigger_id) keys never collide across idle rotations. The payload
        carries only the canonical playback fields the projector's playbacks fold
        reads (the ad_run-only fields it never reads are omitted)."""
        trigger_id = str(uuid.uuid4())
        await self._emit(trigger_id, "playback", "dispatched", {
            **display_scope(display),
            "trigger_id": trigger_id,
            "media_asset_ref": None,
            "dispatched_at": now_iso(),
        })

    def _evict(self, owner) -> None:
        """Program boundary (DONE): drop the owner's cached renders and cancel any
        still-in-flight ones (a late-landing render would otherwise repopulate the
        cache), so a future fresh program for the same subject always gets a fresh
        render — never a prior visit's trigger_id (issue #27)."""
        for key in [k for k in self._cache if k[0] == owner]:
            del self._cache[key]
        for key in [k for k in self._inflight if k[0] == owner]:
            self._inflight.pop(key).cancel()

    def _ensure_render(self, owner, rnd, screen_id=None) -> None:
        key = (owner, rnd)
        if key in self._cache or key in self._inflight:
            return

        async def run():
            try:
                # render returns (trigger_id, urls); cache both together.
                self._cache[key] = await self._render(owner, rnd, screen_id)
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
            self._ensure_render(c.owner, c.round, c.screen_id)

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
