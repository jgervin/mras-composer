import logging
from pathlib import Path

import pytest

import src.cli as cli


def test_default_assets_dir_points_to_existing_kiosk_pool():
    # Base videos are sourced from the kiosk rotation pool (mras-ops/assets).
    d = cli.default_assets_dir()
    assert d.name == "assets" and d.parent.name == "mras-ops"


def test_default_output_dir_is_local_and_not_the_kiosk_pool():
    out = cli.DEFAULT_OUTPUT_DIR
    assert "mras-ops" not in str(out)              # never the rotating pool
    assert str(out).startswith(str(Path.home()))   # local device, easy to find


def test_resolve_output_path_defaults_into_output_dir():
    assert cli.resolve_output_path(None, "/x/clips", "cli-1") == Path("/x/clips/cli-1.mp4")


def test_resolve_output_path_explicit_out_wins():
    assert cli.resolve_output_path("/y/z.mp4", "/x/clips", "cli-1") == Path("/y/z.mp4")


def test_parser_collects_multiple_say_and_draw():
    args = cli.build_parser().parse_args([
        "--say", "250", "Hello, Jason",
        "--say", "1500", "Buy the new Nike smurph shoes",
        "--draw", "500", "LIMITED TIME",
        "--video", "/tmp/ad.mp4",
    ])
    assert args.say == [["250", "Hello, Jason"], ["1500", "Buy the new Nike smurph shoes"]]
    assert args.draw == [["500", "LIMITED TIME"]]
    assert args.video == "/tmp/ad.mp4"


def test_parse_items_converts_marks_to_ints_in_order():
    items = cli.parse_items([["250", "Hello, Jason"], ["1500", "Buy Nike"]])
    assert items == [(250, "Hello, Jason"), (1500, "Buy Nike")]


def test_parse_items_rejects_non_integer_mark():
    with pytest.raises(ValueError):
        cli.parse_items([["soon", "Hello"]])


def test_resolve_base_video_explicit_path(tmp_path):
    vid = tmp_path / "ad.mp4"
    vid.touch()
    assert cli.resolve_base_video(str(vid), None) == vid


def test_resolve_base_video_random_from_assets(tmp_path):
    (tmp_path / "a.mp4").touch()
    (tmp_path / "b.mp4").touch()
    (tmp_path / "notes.txt").touch()

    class FixedRng:
        def choice(self, seq):
            return sorted(seq)[0]  # deterministic

    chosen = cli.resolve_base_video(None, str(tmp_path), rng=FixedRng())
    assert chosen.suffix == ".mp4"
    assert chosen in [tmp_path / "a.mp4", tmp_path / "b.mp4"]


def test_resolve_base_video_errors_when_no_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        cli.resolve_base_video(None, str(tmp_path))  # empty dir, no --video


def test_build_audio_inserts_preserves_marks(tmp_path):
    calls = []

    def fake_synth(text, out_dir):
        calls.append(text)
        return tmp_path / f"{text}.aiff"

    inserts = cli.build_audio_inserts(
        [(250, "Hello"), (1500, "Buy Nike")], tmp_path, synth=fake_synth
    )
    assert inserts == [(tmp_path / "Hello.aiff", 250), (tmp_path / "Buy Nike.aiff", 1500)]
    assert calls == ["Hello", "Buy Nike"]


def test_draw_directives_are_logged_not_rendered(caplog):
    with caplog.at_level(logging.INFO, logger="mras-composer.cli"):
        cli.log_draw_directives([(500, "LIMITED TIME"), (2000, "Act now")])
    text = caplog.text
    assert "LIMITED TIME" in text
    assert "Act now" in text
    assert "500" in text and "2000" in text
    # explicitly flagged as read-but-not-rendered
    assert "not" in text.lower() and "render" in text.lower()
