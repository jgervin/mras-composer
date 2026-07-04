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
# Per-display variants render simultaneously (T-C, owner direction:
# real-time parallel composition). D8's original value was 1.
_FFMPEG_CONCURRENCY = int(os.getenv("FFMPEG_CONCURRENCY", "4"))


def _sem() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(max(1, _FFMPEG_CONCURRENCY))
    return _SEMAPHORE


def _video_filter(overlay_inserts: list[tuple[Path, int, int]], start_index: int):
    """Composite each transparent overlay over the base, shifted to its start (`setpts`) and gated
    to its window (`enable=between`). `eof_action=pass` keeps the base visible after the overlay's
    frames end. Returns (filter_string, final_video_label). `start_index` = ffmpeg input index of
    the first overlay clip (base=0, audio inserts=1..N, overlays=N+1..)."""
    parts: list[str] = []
    prev = "[0:v]"
    for i, (_, start_ms, end_ms) in enumerate(overlay_inserts):
        idx = start_index + i
        s = f"{start_ms / 1000:g}"
        e = f"{end_ms / 1000:g}"
        parts.append(f"[{idx}:v]setpts=PTS+{s}/TB[ov{i}]")
        parts.append(f"{prev}[ov{i}]overlay=0:0:eof_action=pass:enable='between(t,{s},{e})'[v{i}]")
        prev = f"[v{i}]"
    return ";".join(parts), prev


def _audio_filter(offsets: list[int]) -> str:
    """Build the ffmpeg audio graph: each inserted track (inputs 1..N) is delayed by
    its mark (floored at _INSERT_MIN_OFFSET_MS) and mixed over the base audio (input 0).
    `normalize=0` disables amix's default 1/N input scaling, which halved the whole
    personalized clip (-6dB vs idle ads playing the same base at full volume). The base
    peaks near full scale (measured max_volume -0.4dB), so the voice summed on top can
    clip; `alimiter` caps the mix instead. Requires ffmpeg >= 4.4 for amix normalize."""
    parts: list[str] = []
    labels = ["[0:a]"]
    for i, off in enumerate(offsets, start=1):
        off = max(off, _INSERT_MIN_OFFSET_MS)
        parts.append(f"[{i}:a]adelay={off}|{off}[a{i}]")
        labels.append(f"[a{i}]")
    parts.append(f"{''.join(labels)}amix=inputs={len(offsets) + 1}:duration=first:normalize=0[mix]")
    parts.append("[mix]alimiter=limit=0.95[a]")
    return ";".join(parts)


async def assemble(
    base_video: Path,
    audio_inserts: list[tuple[Path, int]],
    trigger_id: str,
    overlay_text: Optional[str] = None,
    overlay_inserts: Optional[list[tuple[Path, int, int]]] = None,
) -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = _OUTPUT_DIR / f"{trigger_id}.mp4"

    async with _sem():
        tmp: Optional[Path] = None
        text_file: Optional[Path] = None
        proc = None
        try:
            tmp = Path(tempfile.mktemp(suffix=".mp4", dir=_OUTPUT_DIR))

            if audio_inserts:
                audio_fc: Optional[str] = _audio_filter([off for _, off in audio_inserts])
                audio_map = "[a]"
            else:
                audio_fc = None
                audio_map = "0:a?"

            if overlay_inserts:
                video_fc, vlabel = _video_filter(overlay_inserts, start_index=1 + len(audio_inserts))
                filter_complex = f"{video_fc};{audio_fc}" if audio_fc else video_fc
                extra_args = ["-filter_complex", filter_complex, "-map", vlabel, "-map", audio_map]
            elif overlay_text:
                # Write text to a temp file to avoid shell-escaping issues with names
                tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
                tf.write(overlay_text)
                tf.close()
                text_file = Path(tf.name)
                drawtext = (
                    f"[0:v]drawtext="
                    f"fontfile={_FONT}:"
                    f"textfile={text_file}:"
                    f"fontsize=12:x=20:y=h-30:fontcolor=white[v]"
                )
                filter_complex = f"{drawtext};{audio_fc}" if audio_fc else drawtext
                extra_args = ["-filter_complex", filter_complex, "-map", "[v]", "-map", audio_map]
            elif audio_fc:
                extra_args = ["-filter_complex", audio_fc, "-map", "0:v", "-map", "[a]"]
            else:
                extra_args = ["-map", "0:v", "-map", "0:a?"]

            inputs: list[str] = ["-i", str(base_video)]
            for path, _ in audio_inserts:
                inputs += ["-i", str(path)]
            for path, _, _ in (overlay_inserts or []):
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
