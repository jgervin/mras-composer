"""Output-conformance checks: a bad custom overlay component must not silently break the composite."""
import pytest

from src.overlay.conformance import ConformanceError, assert_conformant
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
