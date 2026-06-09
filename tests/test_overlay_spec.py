import json

import pytest

from src.overlay.spec import OverlaySpec, default_overlay_spec, parse_overlay_specs


def test_overlay_json_parsed_to_spec():
    raw = json.dumps({
        "text": "LIMITED TIME", "startMs": 500, "durationMs": 2000,
        "preset": "turbulence-warp", "color": "#ff2d2d", "position": "top",
        "fontSize": 120, "fontFamily": "Bangers",
    })
    [spec] = parse_overlay_specs([raw], None)
    assert spec == OverlaySpec(
        text="LIMITED TIME", start_ms=500, duration_ms=2000, preset="turbulence-warp",
        color="#ff2d2d", position="top", font_size=120, font_family="Bangers",
    )
    assert spec.end_ms == 2500


def test_overlay_minimal_json_uses_defaults():
    [spec] = parse_overlay_specs([json.dumps({"text": "Hi", "startMs": 250})], None)
    assert spec.preset == "fade" and spec.duration_ms == 2000
    assert spec.color == "#ffffff" and spec.position == "center"
    assert spec.font_size == 96 and spec.font_family == "Inter"


def test_draw_backcompat_maps_to_fade():
    specs = parse_overlay_specs(None, [["500", "SALE"]])
    assert specs == [OverlaySpec(text="SALE", start_ms=500, preset="fade")]


def test_overlays_then_draws_preserve_order():
    specs = parse_overlay_specs([json.dumps({"text": "A", "startMs": 0})], [["100", "B"]])
    assert [s.text for s in specs] == ["A", "B"]


def test_unknown_preset_raises():
    with pytest.raises(ValueError):
        parse_overlay_specs([json.dumps({"text": "x", "startMs": 0, "preset": "warp9000"})], None)


def test_negative_start_raises():
    with pytest.raises(ValueError):
        parse_overlay_specs([json.dumps({"text": "x", "startMs": -5})], None)


def test_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_overlay_specs(["{not json"], None)


def test_default_overlay_spec_uses_turbulence_warp_defaults(monkeypatch):
    for var in ("OVERLAY_START_MS", "OVERLAY_DURATION_MS", "OVERLAY_PRESET",
                "OVERLAY_COLOR", "OVERLAY_POSITION"):
        monkeypatch.delenv(var, raising=False)
    spec = default_overlay_spec("Jason")
    assert spec.text == "Jason"
    assert spec.preset == "turbulence-warp"
    assert spec.start_ms == 500 and spec.duration_ms == 2000
    assert spec.position == "top"


def test_default_overlay_spec_honors_env(monkeypatch):
    monkeypatch.setenv("OVERLAY_PRESET", "fade")
    monkeypatch.setenv("OVERLAY_START_MS", "1000")
    monkeypatch.setenv("OVERLAY_DURATION_MS", "1500")
    monkeypatch.setenv("OVERLAY_COLOR", "#00ff00")
    monkeypatch.setenv("OVERLAY_POSITION", "bottom")
    spec = default_overlay_spec("Alice")
    assert spec.preset == "fade" and spec.start_ms == 1000 and spec.duration_ms == 1500
    assert spec.color == "#00ff00" and spec.position == "bottom"
