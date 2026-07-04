from pathlib import Path
from unittest.mock import MagicMock, patch

import src.assembly.assembler as asm_mod
from src.assembly.assembler import assemble


def _capture_exec(captured):
    async def fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        out = args[-1]
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            Path(out).touch()
            return (b"", b"")

        proc.communicate = communicate
        return proc

    return fake_exec


def _fc(args):
    return args[args.index("-filter_complex") + 1]


def _maps(args):
    return [args[i + 1] for i, a in enumerate(args) if a == "-map"]


async def test_single_overlay_composited_with_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    overlay = tmp_path / "ov.mov"
    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(tmp_path / "base.mp4", [(tmp_path / "a.mp3", 250)], "t-ov",
                       overlay_inserts=[(overlay, 500, 2500)])

    args = captured["args"]
    fc = _fc(args)
    # overlay input is index 2 (base=0, audio=1); shifted to 0.5s, gated 0.5–2.5s
    assert "[2:v]setpts=PTS+0.5/TB[ov0]" in fc
    assert "[0:v][ov0]overlay=0:0:eof_action=pass:enable='between(t,0.5,2.5)'[v0]" in fc
    # audio graph unchanged
    assert "[1:a]adelay=250|250[a1]" in fc
    assert "amix=inputs=2:duration=first:normalize=0[mix]" in fc
    assert "[mix]alimiter=limit=0.95[a]" in fc
    assert _maps(args) == ["[v0]", "[a]"]
    assert str(overlay) in args  # overlay clip passed as input
    assert "libx264" in args     # output still h264


async def test_two_overlays_chain_and_index_correctly(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    ov1, ov2 = tmp_path / "a.mov", tmp_path / "b.mov"
    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(tmp_path / "base.mp4", [(tmp_path / "au.mp3", 250)], "t-multi",
                       overlay_inserts=[(ov1, 500, 2500), (ov2, 3000, 5000)])

    args = captured["args"]
    fc = _fc(args)
    # base=0, audio=1, overlays=2 and 3; overlays chain [v0] -> [v1]
    assert "[2:v]setpts=PTS+0.5/TB[ov0]" in fc
    assert "[0:v][ov0]overlay=0:0:eof_action=pass:enable='between(t,0.5,2.5)'[v0]" in fc
    assert "[3:v]setpts=PTS+3/TB[ov1]" in fc
    assert "[v0][ov1]overlay=0:0:eof_action=pass:enable='between(t,3,5)'[v1]" in fc
    assert _maps(args) == ["[v1]", "[a]"]
    assert str(ov1) in args and str(ov2) in args


async def test_overlay_inserts_take_precedence_over_overlay_text(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(tmp_path / "base.mp4", [(tmp_path / "a.mp3", 250)], "t-ov2",
                       overlay_text="IGNORED", overlay_inserts=[(tmp_path / "ov.mov", 0, 1000)])
    fc = _fc(captured["args"])
    assert "eof_action=pass" in fc
    assert "drawtext" not in fc


async def test_eof_action_pass_is_present(tmp_path, monkeypatch):
    # regression guard: without eof_action=pass the base would freeze on the overlay's last frame
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(tmp_path / "base.mp4", [(tmp_path / "a.mp3", 250)], "t-ov3",
                       overlay_inserts=[(tmp_path / "ov.mov", 500, 2500)])
    assert "eof_action=pass" in _fc(captured["args"])
