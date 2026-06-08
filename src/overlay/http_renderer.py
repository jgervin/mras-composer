"""Render transparent overlays via the Remotion HTTP sidecar (the live /trigger path).

The composer image is Node-free, so it can't run `npx remotion render` (that's `renderer.py`, used by
the host CLI). Instead it POSTs the same props to the sidecar and writes the returned .mov bytes.
Reuses `_props` so the HTTP and subprocess paths send an identical contract.
"""
import tempfile
from pathlib import Path

from src.overlay.probe import probe_video
from src.overlay.renderer import _props


async def render_overlay_http(spec, base_meta, work_dir, client, sidecar_url) -> Path:
    resp = await client.post(f"{sidecar_url}/render", json=_props(spec, base_meta))
    if resp.status_code != 200:
        raise RuntimeError(f"overlay sidecar returned {resp.status_code}: {resp.text}")
    out = Path(tempfile.mktemp(prefix="overlay-", suffix=".mov", dir=work_dir))
    out.write_bytes(resp.content)
    return out


async def build_overlay_inserts_http(specs, base, work_dir, client, sidecar_url, probe=probe_video):
    """Async analog of cli.build_overlay_inserts: probe the base, render each spec via the sidecar,
    and clamp each overlay window to the base duration."""
    meta = probe(base)
    inserts = []
    for spec in specs:
        clip = await render_overlay_http(spec, meta, work_dir, client, sidecar_url)
        inserts.append((clip, spec.start_ms, min(spec.end_ms, meta.duration_ms)))
    return inserts
