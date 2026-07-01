"""Display-lane emissions from _dispatch_play: the enriched playback/dispatched
(now with screen_kind + dispatched_at + media_asset_ref + ad_run link) and a
thin ad_run/dispatched status transition. Both carry the concrete display."""
import asyncio
from unittest.mock import AsyncMock, patch

import main
from src.orchestrator.model import Round


def _calls(log):
    return [(c.args[2], c.args[3], c.args[4]) for c in log.await_args_list]  # (et, st, payload)


def test_dispatch_emits_enriched_playback_and_ad_run_dispatched():
    db = AsyncMock()
    ws = AsyncMock()
    url = "http://host:8002/media/orch-sp1-opener-0.mp4"
    trigger_id = "3f2a0c9e-1b2c-4d5e-8f90-abcdef012345"
    with patch("main._log", AsyncMock()) as log:
        asyncio.run(main._dispatch_play(db, ws, "display-2", url, "sp1", Round.OPENER, trigger_id))

    calls = _calls(log)
    kinds = [(et, st) for et, st, _ in calls]
    assert ("playback", "dispatched") in kinds
    assert ("ad_run", "dispatched") in kinds

    pb = next(pl for et, st, pl in calls if (et, st) == ("playback", "dispatched"))
    assert pb["screen_kind"] == "display"
    assert pb["screen_id"] == "display-2"
    assert pb["trigger_id"] == trigger_id
    assert pb["ad_run_trigger_id"] == trigger_id
    assert pb["media_asset_ref"] == "orch-sp1-opener-0.mp4"
    assert pb["dispatched_at"]
    # back-compat fields preserved for the Activity Feed
    assert pb["video"] == "orch-sp1-opener-0.mp4"
    assert pb["person"] == "sp1"

    ar = next(pl for et, st, pl in calls if (et, st) == ("ad_run", "dispatched"))
    assert ar["screen_kind"] == "display"
    assert ar["screen_id"] == "display-2"
    assert ar["trigger_id"] == trigger_id
    # started_at means "playback started" — set by ad_run/playing, NOT at dispatch.
    assert "started_at" not in ar

    # trigger_id threaded as the events.trigger_id arg on BOTH, never the person uuid
    for c in log.await_args_list:
        assert c.args[1] == trigger_id
