import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "10"))
_OUTPUT_DIR = Path(os.getenv("ASSEMBLED_OUTPUT_DIR", "/tmp/assembled"))
_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
# Hold the inserted name/speech off the first _INSERT_MIN_OFFSET_MS so it never lands
# inside the kiosk's opening audio crossfade (where it would be muted). Clients are also
# coached to keep the name out of the first 250ms; this is the code-level safety net.
_INSERT_MIN_OFFSET_MS = 250
_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _sem() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(1)
    return _SEMAPHORE


def _audio_filter(offsets: list[int]) -> str:
    """Build the ffmpeg audio graph: each inserted track (inputs 1..N) is delayed by
    its mark (floored at _INSERT_MIN_OFFSET_MS) and mixed over the base audio (input 0)."""
    parts: list[str] = []
    labels = ["[0:a]"]
    for i, off in enumerate(offsets, start=1):
        off = max(off, _INSERT_MIN_OFFSET_MS)
        parts.append(f"[{i}:a]adelay={off}|{off}[a{i}]")
        labels.append(f"[a{i}]")
    parts.append(f"{''.join(labels)}amix=inputs={len(offsets) + 1}:duration=first[a]")
    return ";".join(parts)


async def assemble(
    base_video: Path,
    audio_inserts: list[tuple[Path, int]],
    trigger_id: str,
    overlay_text: Optional[str] = None,
) -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = _OUTPUT_DIR / f"{trigger_id}.mp4"

    async with _sem():
        tmp: Optional[Path] = None
        text_file: Optional[Path] = None
        proc = None
        try:
            tmp = Path(tempfile.mktemp(suffix=".mp4", dir=_OUTPUT_DIR))

            audio_fc = _audio_filter([off for _, off in audio_inserts])
            if overlay_text:
                # Write text to a temp file to avoid shell-escaping issues with names
                tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
                tf.write(overlay_text)
                tf.close()
                text_file = Path(tf.name)
                filter_complex = (
                    f"[0:v]drawtext="
                    f"fontfile={_FONT}:"
                    f"textfile={text_file}:"
                    f"fontsize=12:x=20:y=h-30:fontcolor=white[v];"
                    f"{audio_fc}"
                )
                extra_args = ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
            else:
                extra_args = ["-filter_complex", audio_fc, "-map", "0:v", "-map", "[a]"]

            inputs: list[str] = ["-i", str(base_video)]
            for path, _ in audio_inserts:
                inputs += ["-i", str(path)]

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                *inputs,
                *extra_args,
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
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            if tmp is not None and tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
        finally:
            if text_file is not None and text_file.exists():
                text_file.unlink(missing_ok=True)
