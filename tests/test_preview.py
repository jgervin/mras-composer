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
