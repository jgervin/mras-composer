"""End-to-end tracer bullet: real Remotion render + real ffmpeg composite.

Host-only and slow (renders headless Chromium frames). Excluded from the default run;
invoke with `python -m pytest -m slow`.
"""
import asyncio
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

ASSET = Path("/Users/jn/code/mras-ops/assets/standard.mp4")
OVERLAYS_NODE_MODULES = Path("/Users/jn/code/mras-overlays/node_modules")


def _red_count_top(image_path: Path) -> int:
    from PIL import Image

    im = Image.open(image_path).convert("RGB")
    w, h = im.size
    top = im.crop((0, 0, w, int(h * 0.35)))
    px = top.load()
    return sum(
        1
        for y in range(top.size[1])
        for x in range(0, top.size[0], 2)
        if px[x, y][0] > 150 and px[x, y][1] < 95 and px[x, y][2] < 95
    )


def _frame(src: Path, t: float, dst: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(src), "-frames:v", "1", str(dst)],
                   check=True, capture_output=True)
    return dst


@pytest.mark.skipif(
    not ASSET.exists() or not OVERLAYS_NODE_MODULES.exists(),
    reason="needs a real pool clip and an installed mras-overlays",
)
def test_overlay_renders_and_composites_into_its_window(tmp_path):
    pytest.importorskip("PIL")
    import src.cli as cli

    out = tmp_path / "e2e.mp4"
    asyncio.run(cli.run([
        "--say", "250", "Hi",
        "--overlay", '{"text":"LIMITED TIME","startMs":500,"durationMs":2000,'
                     '"preset":"fade","color":"#ff2d2d","position":"top"}',
        "--video", str(ASSET), "--out", str(out), "--trigger-id", "e2e",
    ]))
    assert out.exists()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,pix_fmt", "-of", "default=nw=1:nk=1", str(out)],
        capture_output=True, text=True,
    ).stdout
    assert "h264" in probe and "yuv420p" in probe  # no alpha leaked into the final mp4

    # Red overlay text appears only inside its 0.5–2.5s window.
    assert _red_count_top(_frame(out, 1.5, tmp_path / "in.png")) > 200
    assert _red_count_top(_frame(out, 5.0, tmp_path / "after.png")) < 50
