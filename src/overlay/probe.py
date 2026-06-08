"""ffprobe a base clip into a VideoMeta so overlays can be rendered to match it."""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoMeta:
    width: int
    height: int
    fps: float
    duration_ms: int


def probe_video(path: Path, runner=subprocess.run) -> VideoMeta:
    proc = runner(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(proc.stdout)
    stream = data["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    return VideoMeta(
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=int(num) / int(den),
        duration_ms=round(float(data["format"]["duration"]) * 1000),
    )
