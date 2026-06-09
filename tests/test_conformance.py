"""Output-conformance checks: a bad custom overlay component must not silently break the composite."""
import subprocess
import pytest

from src.overlay.conformance import ConformanceError, _probe_dims_pixfmt, assert_conformant
from src.overlay.probe import VideoMeta


def _meta(w=854, h=480):
    return VideoMeta(w, h, 24.0, 600)


def test_conformant_passes_for_matching_alpha_clip(tmp_path):
    f = tmp_path / "ov.mov"
    f.write_bytes(b"x")
    assert_conformant(f, _meta(), probe=lambda _p: (854, 480, "yuva444p10le"))


def test_nonalpha_raises(tmp_path):
    f = tmp_path / "ov.mov"
    f.write_bytes(b"x")
    with pytest.raises(ConformanceError):
        assert_conformant(f, _meta(), probe=lambda _p: (854, 480, "yuv420p"))


def test_wrong_dims_raises(tmp_path):
    f = tmp_path / "ov.mov"
    f.write_bytes(b"x")
    with pytest.raises(ConformanceError):
        assert_conformant(f, _meta(), probe=lambda _p: (640, 360, "yuva444p10le"))


def test_probe_dims_pixfmt_raises_conformance_error_on_malformed_output(tmp_path):
    """Two-field stdout (missing pix_fmt) must raise ConformanceError, not ValueError."""
    f = tmp_path / "ov.mov"
    f.write_bytes(b"x")

    class _FakeProc:
        stdout = "854,480\n"  # only 2 fields — missing pix_fmt

    def _fake_runner(*_args, **_kwargs):
        return _FakeProc()

    with pytest.raises(ConformanceError):
        _probe_dims_pixfmt(f, runner=_fake_runner)
