import asyncio
import json
from dataclasses import replace
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
from src.overlay.conformance import assert_conformant
from src.overlay.http_renderer import build_overlay_inserts_http, render_composition_http
from src.overlay.probe import probe_video
from src.overlay.spec import default_overlay_spec
from src.orchestrator.watchdog import Watchdog
from src.selector.selector import select
from src.tts.gateway import synthesize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
_OUTPUT_DIR = Path(os.getenv("ASSEMBLED_OUTPUT_DIR", "/tmp/assembled"))
_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
_HOST = os.getenv("HOST", "localhost")
_PORT = int(os.getenv("PORT", "8002"))
_OVERLAY_SIDECAR_URL = os.getenv("OVERLAY_SIDECAR_URL", "http://mras-overlays:3000")
# Overlay window as a fraction of the base video (owner rule 2026-06-11):
# default = half the clip; the demo rig sets 1.0 = the entire clip.
_OVERLAY_FRACTION = float(os.getenv("OVERLAY_DURATION_FRACTION", "0.5"))


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
        # Unspecified duration = OVERLAY_DURATION_FRACTION of the base clip.
        "durationMs": int(props["durationMs"]) if "durationMs" in props
        else int(meta.duration_ms * _OVERLAY_FRACTION),
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

    from src.orchestrator.core import Orchestrator
    from src.orchestrator.runtime import OrchestratorRuntime
    from src.orchestrator.renderer import Renderer
    displays = [f"display-{i}" for i in range(1, int(os.getenv("DISPLAY_COUNT", "4")) + 1)]
    app.state.orchestrator = Orchestrator(displays)
    renderer = Renderer(
        app.state.db, app.state.http,
        compose=lambda sel, audio, tid, vid: _compose_variant(sel, audio, tid, vid),
        url_for=lambda p: f"http://{_HOST}:{_PORT}/media/{p.name}",
        synthesize=synthesize,
    )

    async def _send_play(display, url, owner, rnd, trigger_id):
        await _dispatch_play(app.state.db, app.state.ws, display, url, owner, rnd, trigger_id)

    async def _send_idle(display):
        await app.state.ws.send_to(display, {"type": "idle"})

    app.state.runtime = OrchestratorRuntime(
        render=renderer.render, send_play=_send_play, send_idle=_send_idle,
        arm_watchdog=lambda d: None, cancel_watchdog=lambda d: None,  # wired below
    )

    # Watchdog: advances a display even if its kiosk never emits clip_ended
    # (dropped WS / dead display). Fires the same clip_ended path after the
    # clip's expected duration + grace.
    async def _fire_clip_ended(display):
        cmds = app.state.orchestrator.on_clip_ended(display)
        await app.state.runtime.apply(cmds)

    # Timeout must safely exceed the longest expected clip so the watchdog never
    # cuts a real clip short — it's a dead-display backstop, not a clip timer.
    # Defaults (clip 30s + 5s grace) clear typical ~10-15s clips with margin; both
    # are env-configurable for tuning.
    watchdog = Watchdog(
        on_timeout=_fire_clip_ended,
        clip_seconds=lambda d: float(os.getenv("CLIP_SECONDS", "30")),
        grace_s=float(os.getenv("WATCHDOG_GRACE_S", "5")),
    )
    app.state.runtime._arm_watchdog = watchdog.arm
    app.state.runtime._cancel_watchdog = watchdog.cancel

    # Periodic tick: expires presence TTLs so people who left free their displays.
    async def _tick_loop():
        while True:
            await asyncio.sleep(float(os.getenv("PRESENCE_TICK_S", "1.0")))
            # Guard each iteration: a raise here must not kill the tick loop, or
            # presence TTLs stop expiring and displays never free up.
            try:
                await app.state.runtime.apply(app.state.orchestrator.tick())
            except Exception:
                logger.exception("tick loop iteration failed")

    app.state.tick_task = asyncio.create_task(_tick_loop())
    yield
    app.state.tick_task.cancel()
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


class PresencePerson(BaseModel):
    uuid: str
    first_seen: str | None = None


class PresencePayload(BaseModel):
    screen_id: str = "screen_0"
    present: list[PresencePerson] = []


@app.post("/presence")
async def presence_endpoint(body: PresencePayload):
    uuids = [p.uuid for p in body.present]
    cmds = app.state.orchestrator.on_presence(uuids)
    await app.state.runtime.apply(cmds)
    return {"status": "ok", "present": len(uuids)}


async def _render_overlay_inserts(selection, trigger_id: str):
    """Best-effort overlays for one selection — any failure ships the ad with
    whatever rendered (never drop the ad). Owner rule: a spoken name is ALWAYS
    also written, so the animated name-text overlay is composited on top of
    every personalized variant, custom-Remotion component or not."""
    inserts = []
    if selection.composition_id:
        try:
            work = Path(tempfile.mkdtemp(prefix="overlay_", dir=_OUTPUT_DIR))
            inserts += await build_custom_overlay_inserts(
                app.state.http, _OVERLAY_SIDECAR_URL, selection.composition_id,
                selection.overlay_props, selection.base_video, work,
            )
        except Exception as exc:
            await _log(app.state.db, trigger_id, "overlay", "error", {"error": str(exc)})
    name = selection.person_name or selection.overlay_text
    if name:
        try:
            meta = probe_video(selection.base_video)
            spec = replace(
                default_overlay_spec(name),
                duration_ms=max(500, int(meta.duration_ms * _OVERLAY_FRACTION)),
            )
            work = Path(tempfile.mkdtemp(prefix="overlay_", dir=_OUTPUT_DIR))
            inserts += await build_overlay_inserts_http(
                [spec], selection.base_video, work, app.state.http, _OVERLAY_SIDECAR_URL
            )
        except Exception as exc:
            await _log(app.state.db, trigger_id, "overlay", "error", {"error": str(exc)})
    return inserts or None


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

    # Standard gate FIRST (cheap query): strangers and blocked people must not
    # enter orchestration (which would reserve displays / spend a render slot).
    gate = await select(body.model_dump(), app.state.db)
    if gate.type == "standard":
        await _log(app.state.db, body.trigger_id, "composition", "standard_selected", {})
        return {"status": "standard"}

    # Temporal orchestration: hand the identity to the pure state machine and
    # apply whatever commands fall out (play the opener now, render-ahead round
    # 2, idle freed displays). The orchestrator owns the display wall — display
    # splitting, round sequencing and rendering live in the orchestrator/runtime
    # (advanced by kiosk clip_ended + the watchdog), not in this one-shot path.
    cmds = app.state.orchestrator.on_identify(body.uuid)
    await app.state.runtime.apply(cmds)
    await _log(app.state.db, body.trigger_id, "composition", "orchestrated",
               {"uuid": body.uuid})
    return {"status": "orchestrated"}


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
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "clip_ended" and msg.get("screen_id"):
                cmds = app.state.orchestrator.on_clip_ended(msg["screen_id"])
                await app.state.runtime.apply(cmds)
    except WebSocketDisconnect:
        app.state.ws.disconnect(ws)


@app.get("/health")
def health():
    return {"status": "ok"}


async def _dispatch_play(db, ws, display, url, owner, rnd, trigger_id) -> None:
    # `ad` drives the kiosk debug badge label. The renderer doesn't surface the
    # selected ad's identifier through the runtime (plumbing it through the URL
    # cache is disproportionate for a debug-only field), so send a best-effort
    # label derived from owner + round, mirroring the renderer's trigger id.
    await ws.send_to(display, {
        "type": "play", "video_url": url, "person": owner,
        "ad": f"orch-{owner}-{rnd.name.lower()}",
    })
    # Record the dispatch so the Activity Feed links the clip and the gaze x
    # playback attention-outcome join has its playback side. The orchestrated
    # runtime replaced the legacy fan-out that was the sole emitter of these.
    # trigger_id is the per-flow id minted by the renderer for THIS composition —
    # NOT the owner/person uuid (person is carried in the payload instead), so the
    # God View's UNIQUE(trigger_id) / UNIQUE(trigger_id, display_id) hold.
    await _log(db, trigger_id, "playback", "dispatched",
               {"video": url.rsplit("/", 1)[-1], "screen_id": display, "person": owner})


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
