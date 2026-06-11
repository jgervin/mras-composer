import asyncio
import json
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.assembly.assembler import _INSERT_MIN_OFFSET_MS, assemble
from src.db import create_pool
from src.display_assignment import DisplayAssigner
from src.overlay.conformance import assert_conformant
from src.overlay.http_renderer import build_overlay_inserts_http, render_composition_http
from src.overlay.probe import probe_video
from src.overlay.spec import default_overlay_spec
from src.selector.selector import select, select_variants
from src.tts.gateway import synthesize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
_OUTPUT_DIR = Path(os.getenv("ASSEMBLED_OUTPUT_DIR", "/tmp/assembled"))
_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
_HOST = os.getenv("HOST", "localhost")
_PORT = int(os.getenv("PORT", "8002"))
_OVERLAY_SIDECAR_URL = os.getenv("OVERLAY_SIDECAR_URL", "http://mras-overlays:3000")
# How long a display stays reserved after being assigned a personalized clip
# (~clip length + compose time); an expired reservation frees it automatically.
_DISPLAY_HOLD_SECS = float(os.getenv("DISPLAY_HOLD_SECS", "12"))


def build_playlist(assets_dir: Path, base_url: str) -> list[str]:
    """Idle-rotation videos: every assets/*.mp4 as a full URL, sorted by name.

    Drop a .mp4 into the assets dir and it joins the kiosk's idle rotation.
    """
    names = sorted(p.name for p in assets_dir.glob("*.mp4"))
    return [f"{base_url}/assets/{name}" for name in names]


class WSManager:
    """Kiosk connections. T-D windows connect as /ws?screen_id=display-<n>
    and can be targeted individually; untagged (legacy) clients still get
    broadcasts but never targeted sends."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, Optional[str]] = {}

    async def connect(self, ws: WebSocket, screen_id: Optional[str] = None) -> None:
        await ws.accept()
        self._clients[ws] = screen_id

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    def screen_ids(self) -> list:
        return sorted({sid for sid in self._clients.values() if sid})

    async def _send(self, targets: list, msg: dict) -> None:
        dead = []
        for ws in targets:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.pop(ws, None)

    async def send_to(self, screen_id: str, msg: dict) -> None:
        await self._send(
            [ws for ws, sid in list(self._clients.items()) if sid == screen_id], msg
        )

    async def broadcast(self, msg: dict) -> None:
        await self._send(list(self._clients), msg)


async def build_custom_overlay_inserts(
    client, sidecar_url, composition_id, props, base, work, probe=probe_video
):
    """Render a custom Remotion composition via the HTTP sidecar and return overlay inserts.

    Probes the base clip to inject canvas dims/fps, renders via sidecar, asserts conformance,
    and returns a single insert tuple clamped to the base duration.
    """
    meta = probe(base)
    enriched = {
        **props,
        "baseWidth": meta.width,
        "baseHeight": meta.height,
        "fps": meta.fps,
        "durationMs": int(props.get("durationMs", 2000)),
    }
    clip = await render_composition_http(client, sidecar_url, composition_id, enriched, work)
    assert_conformant(clip, meta)
    start_ms = int(props.get("startMs", 0))
    duration_ms = enriched["durationMs"]
    return [(clip, start_ms, min(start_ms + duration_ms, meta.duration_ms))]


@asynccontextmanager
async def lifespan(app: FastAPI):
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app.state.db = await create_pool()
    # Generous timeout: spanning a full clip means the sidecar renders more frames (a few–tens of seconds).
    app.state.http = httpx.AsyncClient(timeout=180)
    app.state.ws = WSManager()
    app.state.assigner = DisplayAssigner()
    yield
    await app.state.http.aclose()
    await app.state.db.close()


_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="mras-composer", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"]
)
app.mount("/media", StaticFiles(directory=str(_OUTPUT_DIR)), name="media")
if _ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")


@app.get("/playlist")
def playlist():
    return {"videos": build_playlist(_ASSETS_DIR, f"http://{_HOST}:{_PORT}")}


class TriggerPayload(BaseModel):
    trigger_id: str
    uuid: str | None = None
    confidence: float = 0.0
    is_new_visitor: bool = True
    scene_context: dict = {}
    screen_id: str = "screen_0"
    # People visible in the triggering frame (T-V) — drives display splitting.
    faces_in_frame: int = 1


async def _render_overlay_inserts(selection, trigger_id: str):
    """Best-effort overlay for one selection — failure ships the ad bare."""
    if selection.composition_id:
        try:
            work = Path(tempfile.mkdtemp(prefix="overlay_", dir=_OUTPUT_DIR))
            return await build_custom_overlay_inserts(
                app.state.http, _OVERLAY_SIDECAR_URL, selection.composition_id,
                selection.overlay_props, selection.base_video, work,
            )
        except Exception as exc:
            # Never drop the ad on overlay failure — ship it without the on-screen overlay.
            await _log(app.state.db, trigger_id, "overlay", "error", {"error": str(exc)})
            return None
    if selection.overlay_text:
        try:
            spec = default_overlay_spec(selection.overlay_text)
            work = Path(tempfile.mkdtemp(prefix="overlay_", dir=_OUTPUT_DIR))
            return await build_overlay_inserts_http(
                [spec], selection.base_video, work, app.state.http, _OVERLAY_SIDECAR_URL
            )
        except Exception as exc:
            # Never drop the ad on overlay failure — ship it without the on-screen text.
            await _log(app.state.db, trigger_id, "overlay", "error", {"error": str(exc)})
            return None
    return None


async def _compose_variant(selection, audio_path, trigger_id: str, variant_id: str) -> Path:
    overlay_inserts = await _render_overlay_inserts(selection, trigger_id)
    return await assemble(
        selection.base_video, [(audio_path, _INSERT_MIN_OFFSET_MS)], variant_id,
        overlay_inserts=overlay_inserts,
    )


@app.post("/trigger")
async def trigger_endpoint(body: TriggerPayload):
    screen_ids = app.state.ws.screen_ids()
    if not screen_ids:
        # Legacy single-variant broadcast (no screen_id-tagged kiosk connected).
        return await _trigger_single_broadcast(body)

    assigned = app.state.assigner.assign(
        screen_ids, body.faces_in_frame, time.time(), _DISPLAY_HOLD_SECS
    )
    if not assigned:
        await _log(app.state.db, body.trigger_id, "composition", "no_display", {})
        return {"status": "no_display"}

    selections = await select_variants(body.model_dump(), app.state.db, len(assigned))
    if selections[0].type == "standard":
        # Nothing personalized will play — don't hold the display wall hostage.
        app.state.assigner.release(assigned)
        await _log(app.state.db, body.trigger_id, "composition", "standard_selected", {})
        return {"status": "standard"}

    # One name, one voice clip — shared by every variant (cache-friendly).
    audio_path = await synthesize(
        selections[0].tts_text, selections[0].person_uuid, _VOICE_ID, app.state.http
    )
    if audio_path is None:
        app.state.assigner.release(assigned)
        await _log(app.state.db, body.trigger_id, "tts_attempt", "error",
                   {"error": "TTS_UNAVAILABLE"})
        return {"status": "tts_failed"}
    await _log(app.state.db, body.trigger_id, "tts_attempt", "success", {})

    # Compose every variant IN PARALLEL (owner direction: real-time parallel
    # composition); one failed variant must not sink the others.
    results = await asyncio.gather(
        *[
            _compose_variant(sel, audio_path, body.trigger_id, f"{body.trigger_id}-{i}")
            for i, sel in enumerate(selections)
        ],
        return_exceptions=True,
    )

    sent = 0
    for screen_id, result in zip(assigned, results):
        if isinstance(result, BaseException):
            await _log(app.state.db, body.trigger_id, "assembly", "error",
                       {"screen_id": screen_id, "error": str(result)})
            continue
        video_url = f"http://{_HOST}:{_PORT}/media/{result.name}"
        await app.state.ws.send_to(screen_id, {
            "type": "play",
            "trigger_id": body.trigger_id,
            "video_url": video_url,
        })
        await _log(app.state.db, body.trigger_id, "playback", "dispatched",
                   {"video": result.name, "screen_id": screen_id})
        sent += 1

    if sent == 0:
        app.state.assigner.release(assigned)
        return {"status": "assembly_failed"}
    return {"status": "ok", "displays": sent}


async def _trigger_single_broadcast(body: TriggerPayload):
    selection = await select(body.model_dump(), app.state.db)

    if selection.type == "standard":
        await _log(app.state.db, body.trigger_id, "composition", "standard_selected", {})
        return {"status": "standard"}

    audio_path = await synthesize(
        selection.tts_text,
        selection.person_uuid,
        _VOICE_ID,
        app.state.http,
    )
    if audio_path is None:
        await _log(app.state.db, body.trigger_id, "tts_attempt", "error",
                   {"error": "TTS_UNAVAILABLE"})
        return {"status": "tts_failed"}

    await _log(app.state.db, body.trigger_id, "tts_attempt", "success", {})

    overlay_inserts = await _render_overlay_inserts(selection, body.trigger_id)

    try:
        video_path = await assemble(
            selection.base_video, [(audio_path, _INSERT_MIN_OFFSET_MS)], body.trigger_id,
            overlay_inserts=overlay_inserts,
        )
    except Exception as exc:
        await _log(app.state.db, body.trigger_id, "assembly", "error", {"error": str(exc)})
        return {"status": "assembly_failed"}

    video_url = f"http://{_HOST}:{_PORT}/media/{video_path.name}"
    await app.state.ws.broadcast({
        "type": "play",
        "trigger_id": body.trigger_id,
        "video_url": video_url,
    })
    await _log(app.state.db, body.trigger_id, "playback", "dispatched",
               {"video": video_path.name})
    return {"status": "ok"}


class PreviewPayload(BaseModel):
    component_id: str
    props: dict
    base_video: str


@app.post("/preview")
async def preview_endpoint(body: PreviewPayload):
    try:
        # Lookup is inside the try so a bad/non-UUID component_id (asyncpg DataError) returns
        # a JSON error with CORS headers, not an unhandled 500 (which shows as "Failed to fetch").
        row = await app.state.db.fetchrow(
            "SELECT slug FROM components WHERE id=$1", body.component_id
        )
        if row is None:
            return {"error": "unknown component"}
        # Trim stray whitespace so a copy-paste with a leading/trailing space doesn't make ffprobe fail.
        base_path = Path(body.base_video.strip())
        meta = probe_video(base_path)
        props = {
            **body.props,
            "baseWidth": meta.width,
            "baseHeight": meta.height,
            "fps": meta.fps,
            # Default the overlay to span the whole base clip so the advertiser sees the effect
            # across the preview, not just a 2s flash at the start.
            "durationMs": int(body.props.get("durationMs", meta.duration_ms)),
        }
        work = Path(tempfile.mkdtemp(prefix="preview_", dir=_OUTPUT_DIR))
        clip = await render_composition_http(
            app.state.http, _OVERLAY_SIDECAR_URL, f"comp-{row['slug']}", props, work
        )
        assert_conformant(clip, meta)
        start_ms = int(props.get("startMs", 0))
        inserts = [(clip, start_ms, min(start_ms + props["durationMs"], meta.duration_ms))]
        out = await assemble(
            base_path,
            [],
            f"preview-{int(time.time())}",
            overlay_inserts=inserts,
        )
    except Exception as exc:
        return {"error": str(exc)}

    return {"url": f"http://{_HOST}:{_PORT}/media/{out.name}"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    # T-D kiosk windows identify themselves via ?screen_id=display-<n>.
    await app.state.ws.connect(ws, ws.query_params.get("screen_id"))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        app.state.ws.disconnect(ws)


@app.get("/health")
def health():
    return {"status": "ok"}


async def _log(db, trigger_id: str, event_type: str, status: str, payload: dict) -> None:
    try:
        await db.execute(
            "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
            "VALUES ($1, $2, 'mras-composer', $3, $4, $5::jsonb)",
            trigger_id,
            datetime.now(timezone.utc),
            event_type,
            status,
            json.dumps(payload),
        )
    except Exception as exc:
        logger.error("DB event log failed: %s", exc)
