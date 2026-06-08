"""Terminal entrypoint for assembling a personalized ad clip.

Pass one or more spoken lines (each with a millisecond mark) and an optional
base video; the spoken audio is synthesized locally (macOS `say`) and mixed in
at its mark via the assembler. Animated text overlays are rendered with Remotion
(`--overlay` JSON, or the `--draw` shorthand) and composited onto the video.

Example:
    python -m src.cli \
        --say 250 "Hello, Jason" \
        --overlay '{"text":"LIMITED TIME","startMs":500,"durationMs":2000,"preset":"turbulence-warp"}' \
        --open
"""
import argparse
import asyncio
import logging
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from src.assembly.assembler import assemble
from src.overlay.probe import probe_video
from src.overlay.renderer import render_overlay
from src.overlay.spec import parse_overlay_specs

logger = logging.getLogger("mras-composer.cli")

# Generated clips land here — on the local device, easy to find, and deliberately
# NOT the kiosk rotation pool. Play them yourself; the kiosk never sees them.
DEFAULT_OUTPUT_DIR = Path.home() / "Desktop" / "mras-clips"


def default_assets_dir() -> Path:
    """Base videos are picked from the kiosk rotation pool: <code>/mras-ops/assets."""
    return Path(__file__).resolve().parents[2] / "mras-ops" / "assets"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mras-composer", description="Assemble a personalized ad clip.")
    p.add_argument(
        "--say", nargs=2, action="append", metavar=("MS", "TEXT"),
        help="Spoken line at MS milliseconds. Repeatable.",
    )
    p.add_argument(
        "--draw", nargs=2, action="append", metavar=("MS", "TEXT"),
        help="Shorthand for a default fade text overlay at MS milliseconds. Repeatable.",
    )
    p.add_argument(
        "--overlay", action="append", metavar="JSON",
        help='Animated text overlay as JSON, e.g. \'{"text":"SALE","startMs":500,'
             '"durationMs":2000,"preset":"turbulence-warp"}\'. Repeatable.',
    )
    p.add_argument("--video", help="Base video file. Overrides --assets.")
    p.add_argument(
        "--assets", default=str(default_assets_dir()),
        help="Directory to pick a random base .mp4 from when --video is omitted "
             "(default: the kiosk pool, read-only).",
    )
    p.add_argument("--out", help="Write the clip to this exact path (overrides --out-dir).")
    p.add_argument(
        "--out-dir", default=str(DEFAULT_OUTPUT_DIR),
        help=f"Folder for generated clips (default: {DEFAULT_OUTPUT_DIR}).",
    )
    p.add_argument("--open", action="store_true", help="Open the clip when done (macOS).")
    p.add_argument("--trigger-id", help="Output basename / id (default: cli-<timestamp>).")
    return p


def resolve_output_path(out, out_dir, trigger_id: str) -> Path:
    if out:
        return Path(out).expanduser()
    return Path(out_dir).expanduser() / f"{trigger_id}.mp4"


def parse_items(raw: list[list[str]]) -> list[tuple[int, str]]:
    """Convert argparse [[MS, TEXT], ...] into [(ms_int, text), ...], preserving order."""
    return [(int(mark), text) for mark, text in raw]


def resolve_base_video(video, assets, rng=random) -> Path:
    if video:
        p = Path(video)
        if not p.exists():
            raise FileNotFoundError(f"--video not found: {p}")
        return p
    candidates = sorted(Path(assets).glob("*.mp4")) if assets else []
    if not candidates:
        raise FileNotFoundError(
            f"no .mp4 found in assets dir {assets!r}; pass --video or add clips to --assets"
        )
    return rng.choice(candidates)


def synth_say(text: str, out_dir: Path) -> Path:
    """Synthesize `text` to an audio file using macOS `say`."""
    out = Path(tempfile.mktemp(prefix="say-", suffix=".aiff", dir=out_dir))
    subprocess.run(["say", "-o", str(out), text], check=True)
    return out


def build_audio_inserts(say_items, out_dir, synth=synth_say) -> list[tuple[Path, int]]:
    return [(synth(text, out_dir), ms) for ms, text in say_items]


def build_overlay_inserts(specs, base, work, probe=None, render=None):
    """Render each overlay spec to a transparent clip and pair it with its (start, end) window,
    clamped to the base video's duration."""
    probe = probe or probe_video
    render = render or render_overlay
    meta = probe(base)
    inserts = []
    for spec in specs:
        clip = render(spec, meta, work)
        inserts.append((clip, spec.start_ms, min(spec.end_ms, meta.duration_ms)))
    return inserts


async def run(argv=None) -> Path:
    args = build_parser().parse_args(argv)
    say_items = parse_items(args.say or [])
    if not say_items:
        raise SystemExit("provide at least one --say MS TEXT")
    specs = parse_overlay_specs(args.overlay, args.draw)

    base = resolve_base_video(args.video, args.assets)
    logger.info("base video: %s", base)

    work = Path(tempfile.mkdtemp(prefix="mras_cli_"))
    inserts = build_audio_inserts(say_items, work)
    overlay_inserts = build_overlay_inserts(specs, base, work) if specs else None

    trigger_id = args.trigger_id or f"cli-{int(time.time())}"
    out_path = resolve_output_path(args.out, args.out_dir, trigger_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    produced = await assemble(base, inserts, trigger_id, overlay_inserts=overlay_inserts)
    shutil.move(str(produced), out_path)
    logger.info("assembled: %s", out_path)
    if args.open:
        subprocess.run(["open", str(out_path)], check=False)
    return out_path


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(run(argv)))


if __name__ == "__main__":
    main()
