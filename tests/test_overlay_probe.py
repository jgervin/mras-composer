import json
from pathlib import Path
from unittest.mock import MagicMock

from src.overlay.probe import VideoMeta, probe_video


def _runner(stream, duration):
    out = json.dumps({"streams": [stream], "format": {"duration": duration}})

    def run(args, **kwargs):
        m = MagicMock()
        m.stdout = out
        m.returncode = 0
        return m

    return run


def test_probe_parses_dims_fps_duration():
    r = _runner({"width": 854, "height": 480, "r_frame_rate": "24/1"}, "8.092667")
    meta = probe_video(Path("x.mp4"), runner=r)
    assert meta == VideoMeta(854, 480, 24.0, 8093)


def test_probe_handles_fractional_fps():
    r = _runner({"width": 1920, "height": 1080, "r_frame_rate": "30000/1001"}, "5.0")
    meta = probe_video(Path("x.mp4"), runner=r)
    assert meta.width == 1920 and meta.height == 1080
    assert round(meta.fps, 2) == 29.97
    assert meta.duration_ms == 5000
