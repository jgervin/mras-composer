import json
from pathlib import Path

from src.overlay.probe import VideoMeta
from src.overlay.renderer import render_overlay
from src.overlay.spec import OverlaySpec


def test_render_overlay_builds_prores_argv_and_props(tmp_path):
    captured = {}

    def fake_runner(args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")

        class P:
            returncode = 0
        return P()

    spec = OverlaySpec(text="LIMITED TIME", start_ms=500, duration_ms=2000, preset="fade",
                       color="#ff2d2d", position="top", font_size=120, font_family="Bangers")
    base = VideoMeta(854, 480, 24.0, 8093)

    out = render_overlay(spec, base, tmp_path, runner=fake_runner, overlays_dir="/tmp/ov")

    args = captured["args"]
    assert "remotion" in args and "render" in args and "Overlay" in args
    assert "--codec=prores" in args and "--prores-profile=4444" in args
    assert "--pixel-format=yuva444p10le" in args  # alpha plane (else overlay composites opaque)
    assert str(out) in args and out.suffix == ".mov"
    assert captured["cwd"] == "/tmp/ov"

    props_arg = next(a for a in args if a.startswith("--props="))
    props = json.loads(Path(props_arg.split("=", 1)[1]).read_text())
    assert props["text"] == "LIMITED TIME"
    assert props["preset"] == "fade"
    assert props["baseWidth"] == 854 and props["baseHeight"] == 480
    assert props["fps"] == 24.0 and props["durationMs"] == 2000
    assert props["color"] == "#ff2d2d" and props["fontSize"] == 120 and props["fontFamily"] == "Bangers"


def test_render_overlay_defaults_dir_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MRAS_OVERLAYS_DIR", "/opt/overlays")
    captured = {}

    def fake_runner(args, **kwargs):
        captured["cwd"] = kwargs.get("cwd")

        class P:
            returncode = 0
        return P()

    render_overlay(OverlaySpec("x", 0), VideoMeta(100, 100, 24.0, 1000), tmp_path, runner=fake_runner)
    assert captured["cwd"] == "/opt/overlays"
