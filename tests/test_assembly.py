import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.assembly.assembler as asm_mod
from src.assembly.assembler import assemble


def _one(path, offset=250):
    """A single audio insert at `offset` ms — the common /trigger shape."""
    return [(path, offset)]


async def test_ffmpeg_timeout_raises_and_cleans_up_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_TIMEOUT", 0.05)
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)

    slow_proc = MagicMock()
    slow_proc.returncode = None
    slow_proc.kill = MagicMock()

    async def slow_communicate():
        await asyncio.sleep(100)
        return (b"", b"")

    slow_proc.communicate = slow_communicate

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=slow_proc)):
        with pytest.raises(asyncio.TimeoutError):
            await assemble(tmp_path / "base.mp4", _one(tmp_path / "audio.mp3"), "trig-1")

    assert list(tmp_path.glob("*.mp4")) == []


async def test_ffmpeg_success_returns_named_output(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)

    async def fake_exec(*args, **kwargs):
        out_path = args[-1]
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            Path(out_path).touch()
            return (b"", b"")

        proc.communicate = communicate
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await assemble(tmp_path / "base.mp4", _one(tmp_path / "audio.mp3"), "trig-2")

    assert result == tmp_path / "trig-2.mp4"
    assert result.exists()


async def test_ffmpeg_nonzero_exit_raises_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)

    async def fail_exec(*args, **kwargs):
        proc = MagicMock()
        proc.returncode = 1

        async def communicate():
            return (b"", b"")

        proc.communicate = communicate
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fail_exec):
        with pytest.raises(RuntimeError, match="ffmpeg exited 1"):
            await assemble(tmp_path / "base.mp4", _one(tmp_path / "audio.mp3"), "trig-3")

    assert list(tmp_path.glob("*.mp4")) == []


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


def _filter_complex(args):
    return args[args.index("-filter_complex") + 1]


async def test_single_insert_delayed_past_first_250ms(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(tmp_path / "base.mp4", _one(tmp_path / "audio.mp3"), "trig-delay")

    # Input 1 is the inserted name/speech; it must not sound in the first 250ms.
    fc = _filter_complex(captured["args"])
    assert "[1:a]adelay=250|250[a1]" in fc
    assert "[0:a][a1]amix=inputs=2:duration=first[a]" in fc


async def test_multiple_inserts_each_delayed_to_its_own_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    inserts = [(tmp_path / "name.aiff", 250), (tmp_path / "promo.aiff", 1500)]
    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(tmp_path / "base.mp4", inserts, "trig-multi")

    args = captured["args"]
    fc = _filter_complex(args)
    assert "[1:a]adelay=250|250[a1]" in fc
    assert "[2:a]adelay=1500|1500[a2]" in fc
    assert "[0:a][a1][a2]amix=inputs=3:duration=first[a]" in fc
    # both inserts are passed to ffmpeg as inputs
    assert str(tmp_path / "name.aiff") in args
    assert str(tmp_path / "promo.aiff") in args


async def test_offset_below_floor_is_clamped_to_250(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(tmp_path / "base.mp4", _one(tmp_path / "audio.mp3", offset=100), "trig-clamp")

    # 100ms is below the 250ms floor, so it is raised to 250.
    assert "[1:a]adelay=250|250[a1]" in _filter_complex(captured["args"])


async def test_overlay_text_still_drawn_alongside_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    captured = {}
    with patch("asyncio.create_subprocess_exec", side_effect=_capture_exec(captured)):
        await assemble(
            tmp_path / "base.mp4", _one(tmp_path / "audio.mp3"), "trig-overlay", overlay_text="Jordan"
        )

    fc = _filter_complex(captured["args"])
    assert "[1:a]adelay=250|250[a1]" in fc
    assert "drawtext" in fc  # overlay text still applied alongside the delay


async def test_semaphore_serializes_concurrent_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(asm_mod, "_OUTPUT_DIR", tmp_path)
    events: list[str] = []

    async def ordered_proc(*args, **kwargs):
        out_path = args[-1]
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            events.append("start")
            await asyncio.sleep(0.05)
            Path(out_path).touch()
            events.append("end")
            return (b"", b"")

        proc.communicate = communicate
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=ordered_proc):
        t1 = asyncio.create_task(
            assemble(tmp_path / "base.mp4", _one(tmp_path / "audio.mp3"), "t1")
        )
        t2 = asyncio.create_task(
            assemble(tmp_path / "base.mp4", _one(tmp_path / "audio.mp3"), "t2")
        )
        await asyncio.gather(t1, t2)

    assert events == ["start", "end", "start", "end"]
