"""POST /preview: render a custom component over a base video (no audio).

Patches out every I/O collaborator; verifies the endpoint wires render →
conformance → assemble correctly and returns a media URL.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import main


def _meta(width=1920, height=1080, fps=30.0, duration_ms=5000):
    m = MagicMock()
    m.width = width
    m.height = height
    m.fps = fps
    m.duration_ms = duration_ms
    return m


def _preview_client(*, slug="neon", component_missing=False):
    """Build a TestClient with /preview collaborators patched. Returns (client, mocks)."""
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=None if component_missing else {"slug": slug})
    db.execute = AsyncMock()
    db.close = AsyncMock()

    out_path = Path("/tmp/assembled/preview-12345.mp4")
    assemble_mock = AsyncMock(return_value=out_path)
    clip_path = Path("/tmp/preview_work/overlay-abc.mov")
    render_mock = AsyncMock(return_value=clip_path)
    probe_mock = MagicMock(return_value=_meta())
    conformant_mock = MagicMock()

    patches = [
        patch("main.create_pool", AsyncMock(return_value=db)),
        patch("main.assemble", assemble_mock),
        patch("main.render_composition_http", render_mock),
        patch("main.probe_video", probe_mock),
        patch("main.assert_conformant", conformant_mock),
    ]
    for p in patches:
        p.start()

    client = TestClient(main.app)
    return client, {
        "assemble": assemble_mock,
        "render": render_mock,
        "probe": probe_mock,
        "conformant": conformant_mock,
        "db": db,
        "patches": patches,
        "out_path": out_path,
    }


def _stop(mocks):
    for p in mocks["patches"]:
        p.stop()


def test_preview_bad_component_id_is_graceful_not_500():
    # A non-UUID component_id makes the DB lookup raise (asyncpg DataError in prod).
    # /preview must catch it and return JSON {"error": ...} with 200 — NOT an unhandled
    # 500, which carries no CORS header and shows as "Failed to fetch" in the browser.
    db = AsyncMock()

    async def _fetchrow(q, *a):
        if "information_schema" in q:  # lifespan's ads.targeting probe (TODO-7)
            return None
        raise Exception("invalid UUID 'comp-fallingsnow'")

    db.fetchrow = AsyncMock(side_effect=_fetchrow)
    db.execute = AsyncMock()
    db.close = AsyncMock()
    p = patch("main.create_pool", AsyncMock(return_value=db))
    p.start()
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        with client:
            res = client.post(
                "/preview",
                json={"component_id": "comp-fallingsnow", "props": {}, "base_video": "/assets/standard.mp4"},
            )
        assert res.status_code == 200, f"expected graceful 200, got {res.status_code}"
        assert "error" in res.json()
    finally:
        p.stop()


def test_preview_overlay_spans_full_base_duration_by_default():
    # When durationMs is omitted, the preview overlay should span the whole base clip so the
    # advertiser actually sees the effect — not a 2s flash at the start of a longer clip.
    client, mocks = _preview_client()  # probe returns _meta(duration_ms=5000)
    try:
        with client:
            res = client.post(
                "/preview",
                json={
                    "component_id": "a1b2c3d4-0000-0000-0000-000000000001",
                    "props": {},
                    "base_video": "/assets/standard.mp4",
                },
            )
        assert res.status_code == 200
        # render_composition_http(client, url, composition_id, props, work) → props is arg index 3
        render_args, _ = mocks["render"].call_args
        rendered_props = render_args[3]
        assert rendered_props["durationMs"] == 5000  # base duration, not the old 2000 default
    finally:
        _stop(mocks)


def test_preview_strips_whitespace_from_base_video():
    # A stray leading/trailing space in the base path makes ffprobe fail with a cryptic error.
    # /preview must trim it before probing/compositing.
    client, mocks = _preview_client()
    try:
        with client:
            res = client.post(
                "/preview",
                json={
                    "component_id": "a1b2c3d4-0000-0000-0000-000000000001",
                    "props": {"durationMs": 2000},
                    "base_video": "  /assets/standard.mp4  ",
                },
            )
        assert res.status_code == 200
        # probe_video was called with the trimmed path (no surrounding whitespace)
        probe_args, _ = mocks["probe"].call_args
        assert str(probe_args[0]) == "/assets/standard.mp4"
        # assemble likewise got the trimmed path
        assemble_args, _ = mocks["assemble"].call_args
        assert str(assemble_args[0]) == "/assets/standard.mp4"
    finally:
        _stop(mocks)


def test_preview_renders_and_composites():
    client, mocks = _preview_client()
    try:
        with client:
            res = client.post(
                "/preview",
                json={
                    "component_id": "a1b2c3d4-0000-0000-0000-000000000001",
                    "props": {"durationMs": 2000},
                    "base_video": "/assets/standard.mp4",
                },
            )
        assert res.status_code == 200
        body = res.json()
        assert "url" in body, f"expected 'url' key in {body}"
        assert mocks["out_path"].name in body["url"], (
            f"expected {mocks['out_path'].name!r} in url {body['url']!r}"
        )
        # render was called exactly once (composition rendered via sidecar)
        mocks["render"].assert_awaited_once()
        # render was called with a composition id of the form comp-<slug>
        render_args, _ = mocks["render"].call_args
        comp_id = render_args[2]
        assert comp_id.startswith("comp-"), (
            f"expected composition id to start with 'comp-', got {comp_id!r}"
        )
        # assemble was called with overlay_inserts (not None)
        mocks["assemble"].assert_awaited_once()
        _, kwargs = mocks["assemble"].call_args
        assert kwargs.get("overlay_inserts") is not None, (
            "assemble must receive overlay_inserts"
        )
    finally:
        _stop(mocks)


def test_preview_missing_base_video_returns_friendly_error():
    # If the base clip can't be probed (e.g. a stale ad pointing at /assets/standard1.mp4 that
    # doesn't exist), /preview should return a human error — not leak the raw ffprobe command —
    # and must not attempt to render or composite.
    import subprocess

    client, mocks = _preview_client()
    mocks["probe"].side_effect = subprocess.CalledProcessError(
        1, ["ffprobe", "-v", "error", "/assets/standard1.mp4"]
    )
    try:
        with client:
            res = client.post(
                "/preview",
                json={
                    "component_id": "a1b2c3d4-0000-0000-0000-000000000001",
                    "props": {},
                    "base_video": "/assets/standard1.mp4",
                },
            )
        assert res.status_code == 200
        err = res.json().get("error", "")
        assert "/assets/standard1.mp4" in err
        assert "not found" in err.lower() or "unreadable" in err.lower()
        assert "ffprobe" not in err.lower()  # raw command not leaked to the user
        mocks["render"].assert_not_called()
        mocks["assemble"].assert_not_called()
    finally:
        _stop(mocks)


def test_preview_unknown_component_returns_error():
    client, mocks = _preview_client(component_missing=True)
    try:
        with client:
            res = client.post(
                "/preview",
                json={
                    "component_id": "a1b2c3d4-0000-0000-0000-000000000099",
                    "props": {},
                    "base_video": "/assets/standard.mp4",
                },
            )
        assert res.status_code == 200
        body = res.json()
        assert "error" in body, f"expected 'error' key in {body}"
        mocks["render"].assert_not_called()
        mocks["assemble"].assert_not_called()
    finally:
        _stop(mocks)


def test_preview_conformance_failure_returns_error():
    client, mocks = _preview_client()
    mocks["conformant"].side_effect = ValueError("bad dimensions")
    try:
        with client:
            res = client.post(
                "/preview",
                json={
                    "component_id": "a1b2c3d4-0000-0000-0000-000000000001",
                    "props": {"durationMs": 2000},
                    "base_video": "/assets/standard.mp4",
                },
            )
        assert res.status_code == 200
        body = res.json()
        assert "error" in body, f"expected 'error' key in {body}"
        mocks["assemble"].assert_not_called()
    finally:
        _stop(mocks)
