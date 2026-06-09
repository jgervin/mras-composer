"""Output-conformance check: verify a rendered overlay .mov has matching dims and alpha pixel format."""
import subprocess
from pathlib import Path


class ConformanceError(Exception):
    pass


def _probe_dims_pixfmt(path: Path, runner=subprocess.run):
    proc = runner(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,pix_fmt",
            "-of", "csv=p=0:s=,", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    parts = proc.stdout.strip().split(",", 2)
    if len(parts) != 3:
        raise ConformanceError(f"ffprobe returned unexpected output: {proc.stdout!r}")
    w, h, pix = parts
    return int(w), int(h), pix


def assert_conformant(mov: Path, base_meta, probe=_probe_dims_pixfmt) -> None:
    w, h, pix = probe(mov)
    if w != base_meta.width or h != base_meta.height:
        raise ConformanceError(
            f"overlay dims {w}x{h} do not match base {base_meta.width}x{base_meta.height}"
        )
    if not pix.startswith("yuva"):
        raise ConformanceError(
            f"overlay pixel format {pix!r} has no alpha channel (expected yuva*)"
        )
