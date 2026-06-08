import json
from pathlib import Path

import src.cli as cli
from src.overlay.probe import VideoMeta
from src.overlay.spec import OverlaySpec


def test_build_overlay_inserts_renders_each_and_clamps_to_base_duration(tmp_path):
    specs = [
        OverlaySpec(text="A", start_ms=500, duration_ms=2000),    # end 2500
        OverlaySpec(text="B", start_ms=7000, duration_ms=5000),   # end 12000 → clamp to 8000
    ]
    meta = VideoMeta(854, 480, 24.0, 8000)
    rendered = []

    def fake_render(spec, base_meta, work, **kw):
        rendered.append(spec.text)
        return tmp_path / f"{spec.text}.mov"

    inserts = cli.build_overlay_inserts(
        specs, Path("base.mp4"), tmp_path, probe=lambda b: meta, render=fake_render
    )
    assert inserts == [(tmp_path / "A.mov", 500, 2500), (tmp_path / "B.mov", 7000, 8000)]
    assert rendered == ["A", "B"]


async def test_run_renders_overlays_and_threads_into_assemble(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_audio_inserts", lambda items, work, **k: [(tmp_path / "a.aiff", 250)])
    monkeypatch.setattr(cli, "probe_video", lambda b: VideoMeta(854, 480, 24.0, 8000))
    monkeypatch.setattr(cli, "render_overlay", lambda spec, meta, work, **k: tmp_path / f"{spec.text}.mov")

    captured = {}

    async def fake_assemble(base, audio, trigger_id, overlay_text=None, overlay_inserts=None):
        captured["overlay_inserts"] = overlay_inserts
        out = tmp_path / "produced.mp4"
        out.touch()
        return out

    monkeypatch.setattr(cli, "assemble", fake_assemble)

    base = tmp_path / "base.mp4"
    base.touch()
    out = await cli.run([
        "--say", "250", "Hi",
        "--overlay", json.dumps({"text": "SALE", "startMs": 500, "durationMs": 2000}),
        "--video", str(base), "--out", str(tmp_path / "o.mp4"),
    ])
    assert captured["overlay_inserts"] == [(tmp_path / "SALE.mov", 500, 2500)]
    assert out == tmp_path / "o.mp4"


async def test_run_draw_backcompat_renders_a_fade_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_audio_inserts", lambda items, work, **k: [(tmp_path / "a.aiff", 250)])
    monkeypatch.setattr(cli, "probe_video", lambda b: VideoMeta(854, 480, 24.0, 8000))
    seen = {}

    def fake_render(spec, meta, work, **k):
        seen["preset"] = spec.preset
        seen["text"] = spec.text
        return tmp_path / "d.mov"

    monkeypatch.setattr(cli, "render_overlay", fake_render)

    async def fake_assemble(base, audio, trigger_id, overlay_text=None, overlay_inserts=None):
        captured = tmp_path / "produced.mp4"
        captured.touch()
        return captured

    monkeypatch.setattr(cli, "assemble", fake_assemble)

    base = tmp_path / "base.mp4"
    base.touch()
    await cli.run(["--say", "250", "Hi", "--draw", "500", "LIMITED TIME",
                   "--video", str(base), "--out", str(tmp_path / "o.mp4")])
    assert seen == {"preset": "fade", "text": "LIMITED TIME"}
