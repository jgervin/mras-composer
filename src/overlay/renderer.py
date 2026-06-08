"""Render a transparent animated overlay (.mov, ProRes 4444) via the mras-overlays Remotion project.

The Python composer only orchestrates the render: it builds props from the spec + base video meta,
writes them to a temp file (avoids shell-escaping), and shells out to `npx remotion render`.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from src.overlay.probe import VideoMeta
from src.overlay.spec import OverlaySpec

_DEFAULT_OVERLAYS_DIR = "/Users/jn/code/mras-overlays"
_RENDER_TIMEOUT = int(os.getenv("OVERLAY_RENDER_TIMEOUT", "180"))


def _props(spec: OverlaySpec, base: VideoMeta) -> dict:
    return {
        "text": spec.text,
        "preset": spec.preset,
        "color": spec.color,
        "position": spec.position,
        "fontSize": spec.font_size,
        "fontFamily": spec.font_family,
        "baseWidth": base.width,
        "baseHeight": base.height,
        "fps": base.fps,
        "durationMs": spec.duration_ms,
    }


def render_overlay(spec, base_meta, work_dir, runner=subprocess.run, overlays_dir=None) -> Path:
    overlays_dir = overlays_dir or os.getenv("MRAS_OVERLAYS_DIR", _DEFAULT_OVERLAYS_DIR)
    work_dir = Path(work_dir)
    props_file = Path(tempfile.mktemp(prefix="props-", suffix=".json", dir=work_dir))
    out = Path(tempfile.mktemp(prefix="overlay-", suffix=".mov", dir=work_dir))
    props_file.write_text(json.dumps(_props(spec, base_meta)))

    runner(
        [
            "npx", "remotion", "render", "src/index.ts", "Overlay", str(out),
            f"--props={props_file}", "--codec=prores", "--prores-profile=4444",
            "--pixel-format=yuva444p10le", "--log=error",
        ],
        cwd=str(overlays_dir), check=True, timeout=_RENDER_TIMEOUT,
    )
    return out
