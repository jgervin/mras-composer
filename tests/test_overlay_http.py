"""HTTP overlay source: render via the Remotion sidecar instead of a local `npx` subprocess.

Mirrors test_overlay_renderer.py (which covers the local-subprocess path) but for the live /trigger
path, where the Node-free composer image must call the sidecar over HTTP.
"""
import pytest

from src.overlay.http_renderer import build_overlay_inserts_http, render_overlay_http
from src.overlay.probe import VideoMeta
from src.overlay.renderer import _props
from src.overlay.spec import OverlaySpec


class FakeResp:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    async def post(self, url, json=None):
        self.calls.append((url, json))
        return self._resp


async def test_render_overlay_http_posts_props_and_saves_bytes(tmp_path):
    client = FakeClient(FakeResp(200, content=b"FAKE-MOV-BYTES"))
    spec = OverlaySpec(text="Jason", start_ms=500, duration_ms=2000, preset="turbulence-warp")
    base = VideoMeta(854, 480, 24.0, 8093)

    out = await render_overlay_http(spec, base, tmp_path, client, "http://sidecar:3000")

    # Posts the exact props contract the sidecar/Remotion expects, to the /render endpoint.
    assert client.calls == [("http://sidecar:3000/render", _props(spec, base))]
    assert out.suffix == ".mov"
    assert out.read_bytes() == b"FAKE-MOV-BYTES"


async def test_render_overlay_http_raises_on_non_200(tmp_path):
    client = FakeClient(FakeResp(500, text="boom"))
    spec = OverlaySpec(text="x", start_ms=0)
    base = VideoMeta(100, 100, 24.0, 1000)

    with pytest.raises(RuntimeError):
        await render_overlay_http(spec, base, tmp_path, client, "http://sidecar:3000")


async def test_build_overlay_inserts_http_renders_each_and_clamps_window(tmp_path):
    client = FakeClient(FakeResp(200, content=b"MOV"))
    # Base is only 1000ms long; the overlay window (0..3000) must clamp to 1000.
    meta = VideoMeta(640, 360, 24.0, 1000)
    specs = [OverlaySpec(text="Jason", start_ms=0, duration_ms=3000, preset="fade")]

    inserts = await build_overlay_inserts_http(
        specs, tmp_path / "base.mp4", tmp_path, client, "http://sidecar:3000",
        probe=lambda _path: meta,
    )

    assert len(inserts) == 1
    clip, start_ms, end_ms = inserts[0]
    assert clip.read_bytes() == b"MOV"
    assert start_ms == 0
    assert end_ms == 1000  # clamped to base duration
