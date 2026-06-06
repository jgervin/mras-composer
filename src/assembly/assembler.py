import asyncio
import os
import tempfile
from pathlib import Path

_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "10"))
_OUTPUT_DIR = Path(os.getenv("ASSEMBLED_OUTPUT_DIR", "/tmp/assembled"))
_SEMAPHORE: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(1)
    return _SEMAPHORE


async def assemble(base_video: Path, audio: Path, trigger_id: str) -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = _OUTPUT_DIR / f"{trigger_id}.mp4"

    async with _sem():
        tmp = Path(tempfile.mktemp(suffix=".mp4", dir=_OUTPUT_DIR))
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", str(base_video), "-i", str(audio),
                "-filter_complex", "amix=inputs=2:duration=first",
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "aac",
                str(tmp),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg exited {proc.returncode}")
            tmp.rename(out)
            return out
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
