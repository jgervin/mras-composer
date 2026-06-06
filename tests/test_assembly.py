import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.assembly.assembler as asm_mod
from src.assembly.assembler import assemble


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
            await assemble(tmp_path / "base.mp4", tmp_path / "audio.mp3", "trig-1")

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
        result = await assemble(tmp_path / "base.mp4", tmp_path / "audio.mp3", "trig-2")

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
            await assemble(tmp_path / "base.mp4", tmp_path / "audio.mp3", "trig-3")

    assert list(tmp_path.glob("*.mp4")) == []


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
            assemble(tmp_path / "base.mp4", tmp_path / "audio.mp3", "t1")
        )
        t2 = asyncio.create_task(
            assemble(tmp_path / "base.mp4", tmp_path / "audio.mp3", "t2")
        )
        await asyncio.gather(t1, t2)

    assert events == ["start", "end", "start", "end"]
