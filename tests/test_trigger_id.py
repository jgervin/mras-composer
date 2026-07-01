"""trigger_id propagation invariant.

A trigger_id identifies ONE personalization->composition->playback flow. It must
be a per-flow id (a real uuid), stable across every event of that flow, and
DISTINCT from the person/owner uuid. The God View schema enforces this: ad_runs.
trigger_id and playbacks.(trigger_id, display_id) are NOT NULL UNIQUE, so reusing
the person uuid as trigger_id makes the same person triggering twice collide.

These tests drive a single trigger through the render -> runtime -> dispatch seam
with fakes (no live Postgres, no running service) and assert the trigger_id
written to the composition and playback events is a valid uuid, distinct from the
owner, and shared across both events.
"""
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from src.orchestrator.renderer import Renderer
from src.orchestrator.runtime import OrchestratorRuntime
from src.orchestrator.commands import Play
from src.orchestrator.model import Round
from src.selector.selector import AdSelection


def _is_uuid(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _sel(name="Jason"):
    return AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                       tts_text=f"Welcome, {name}!", person_uuid="u1",
                       overlay_text=name, person_name=name)


async def test_trigger_id_is_uuid_distinct_from_owner_and_shared_render_to_dispatch():
    owner = "u1"
    captured = {"compose_tids": [], "dispatched_tids": []}

    async def fake_compose(sel, audio, trigger_id, variant_id):
        captured["compose_tids"].append(trigger_id)
        return Path(f"/tmp/{variant_id}.mp4")

    async def fake_send_play(display, url, owner_, rnd, trigger_id=None):
        captured["dispatched_tids"].append(trigger_id)

    renderer = Renderer(
        db=AsyncMock(), http=AsyncMock(), compose=fake_compose,
        url_for=lambda p: f"http://c/media/{p.name}",
        synthesize=AsyncMock(return_value=Path("/tmp/a.wav")),
    )
    rt = OrchestratorRuntime(
        render=renderer.render, send_play=fake_send_play,
        send_idle=AsyncMock(), arm_watchdog=Mock(), cancel_watchdog=Mock(),
    )

    with patch("src.orchestrator.renderer.select", AsyncMock(return_value=_sel())):
        # cache miss -> renderer runs -> pending display resumes -> send_play fires
        await rt.apply([Play("display-1", owner, Round.OPENER, 0)])
        await rt.drain()

    # a playback was dispatched
    assert captured["dispatched_tids"], "no play dispatched to a display"
    pb_tid = captured["dispatched_tids"][0]

    # (a) valid uuid  (b) distinct from the person/owner
    assert _is_uuid(pb_tid), f"playback trigger_id is not a uuid: {pb_tid!r}"
    assert pb_tid != owner, "playback trigger_id must not be the person/owner uuid"

    # (c) composition and playback of the same flow share ONE trigger_id
    assert captured["compose_tids"], "composition never received a trigger_id"
    assert _is_uuid(captured["compose_tids"][0]), \
        f"composition trigger_id is not a uuid: {captured['compose_tids'][0]!r}"
    all_tids = set(captured["compose_tids"]) | set(captured["dispatched_tids"])
    assert all_tids == {pb_tid}, f"events did not share one trigger_id: {all_tids}"


async def test_two_triggers_for_same_owner_get_distinct_trigger_ids():
    """Same person, two separate render flows -> two different trigger_ids, so the
    God View UNIQUE(trigger_id) constraints do not collide."""
    renderer = Renderer(
        db=AsyncMock(), http=AsyncMock(),
        compose=AsyncMock(side_effect=lambda sel, audio, tid, vid: Path(f"/tmp/{vid}.mp4")),
        url_for=lambda p: f"http://c/media/{p.name}",
        synthesize=AsyncMock(return_value=Path("/tmp/a.wav")),
    )
    with patch("src.orchestrator.renderer.select", AsyncMock(return_value=_sel())):
        tid1, _ = await renderer.render("u1", Round.OPENER)
        tid2, _ = await renderer.render("u1", Round.OPENER)
    assert _is_uuid(tid1) and _is_uuid(tid2)
    assert tid1 != tid2, "each render flow must mint a fresh trigger_id"
