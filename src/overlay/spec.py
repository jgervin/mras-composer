"""Overlay directive spec: advertiser JSON (camelCase) → OverlaySpec, plus --draw back-compat."""
import json
from dataclasses import dataclass

_DEFAULT_DURATION_MS = 2000
PRESETS = {"fade", "turbulence-warp"}


@dataclass(frozen=True)
class OverlaySpec:
    text: str
    start_ms: int
    duration_ms: int = _DEFAULT_DURATION_MS
    preset: str = "fade"
    color: str = "#ffffff"
    position: str = "center"
    font_size: int = 96
    font_family: str = "Inter"

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


def _spec_from_json(raw: str) -> OverlaySpec:
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid --overlay JSON: {exc}") from exc

    preset = d.get("preset", "fade")
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    start_ms = int(d["startMs"])
    if start_ms < 0:
        raise ValueError("startMs must be >= 0")

    return OverlaySpec(
        text=d["text"],
        start_ms=start_ms,
        duration_ms=int(d.get("durationMs", _DEFAULT_DURATION_MS)),
        preset=preset,
        color=d.get("color", "#ffffff"),
        position=d.get("position", "center"),
        font_size=int(d.get("fontSize", 96)),
        font_family=d.get("fontFamily", "Inter"),
    )


def parse_overlay_specs(overlay_raw, draw_raw) -> list[OverlaySpec]:
    """`overlay_raw`: list of --overlay JSON strings. `draw_raw`: list of [MS, TEXT] (legacy --draw,
    mapped to a default `fade` overlay)."""
    specs = [_spec_from_json(s) for s in (overlay_raw or [])]
    for mark, text in (draw_raw or []):
        specs.append(OverlaySpec(text=text, start_ms=int(mark), preset="fade"))
    return specs
