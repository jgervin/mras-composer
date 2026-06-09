"""No-audio branch: assemble(audio_inserts=[]) must NOT emit amix.

Covers the /preview use-case where the composer overlays a video clip but there
are no speech/audio inserts to mix in — only the base video's own audio is kept.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import src.assembly.assembler as asm_mod
from src.assembly.assembler import assemble


def _capture_exec(captured):
    async def fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        out_path = args[-1]
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            Path(out_path).touch()
            return (b"", b"")

        proc.communicate = communicate
        return proc

    return fake_exec


async def test_no_audio_with_overlay_inserts_uses_video_filter_and_passthrough_audio(
    tmp_path, monkeypatch
):
    """overlay_inserts + no audio_inserts: video overlay filter present, -map 0:a? used, no amix."""
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    clip = tmp_path / "clip.mov"
    clip.touch()

    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(
            tmp_path / "base.mp4",
            [],
            "p1",
            overlay_inserts=[(clip, 0, 1000)],
        )

    args = captured["args"]
    # No amix anywhere in the arg list
    assert all("amix" not in str(a) for a in args), f"amix found in args: {args}"
    # Video overlay filter is present
    fc_idx = args.index("-filter_complex")
    fc = args[fc_idx + 1]
    assert "overlay" in fc, f"expected overlay in filter_complex: {fc!r}"
    # Base audio is passed through as optional stream (0:a? tolerates audio-less clips)
    assert "0:a?" in args, f"expected '0:a?' in args: {args}"


async def test_no_audio_no_overlays_maps_streams_directly(tmp_path, monkeypatch):
    """No audio_inserts, no overlay_inserts, no overlay_text: pure stream copy, no filter_complex."""
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}

    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(tmp_path / "base.mp4", [], "p2")

    args = captured["args"]
    assert all("amix" not in str(a) for a in args), f"amix found in args: {args}"
    assert "-filter_complex" not in args, f"unexpected -filter_complex in args: {args}"
    assert "0:v" in args
    assert "0:a?" in args
