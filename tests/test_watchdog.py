import asyncio
from unittest.mock import AsyncMock, Mock
import pytest

from src.orchestrator.watchdog import Watchdog


async def test_watchdog_fires_clip_ended_after_duration():
    on_timeout = AsyncMock()
    wd = Watchdog(on_timeout=on_timeout, grace_s=0.0, clip_seconds=lambda d: 0.01)
    wd.arm("display-1")
    await asyncio.sleep(0.05)
    on_timeout.assert_awaited_once_with("display-1")


async def test_watchdog_cancel_prevents_fire():
    on_timeout = AsyncMock()
    wd = Watchdog(on_timeout=on_timeout, grace_s=0.0, clip_seconds=lambda d: 0.05)
    wd.arm("display-1")
    wd.cancel("display-1")
    await asyncio.sleep(0.1)
    on_timeout.assert_not_awaited()
