import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.assembly.assembler import _INSERT_MIN_OFFSET_MS, assemble
from src.db import create_pool
from src.selector.selector import select
from src.tts.gateway import synthesize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
_OUTPUT_DIR = Path(os.getenv("ASSEMBLED_OUTPUT_DIR", "/tmp/assembled"))
_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
_HOST = os.getenv("HOST", "localhost")
_PORT = int(os.getenv("PORT", "8002"))


class WSManager:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        dead: Set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead


@asynccontextmanager
async def lifespan(app: FastAPI):
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app.state.db = await create_pool()
    app.state.http = httpx.AsyncClient()
    app.state.ws = WSManager()
    yield
    await app.state.http.aclose()
    await app.state.db.close()


_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="mras-composer", lifespan=lifespan)
app.mount("/media", StaticFiles(directory=str(_OUTPUT_DIR)), name="media")
if _ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")


class TriggerPayload(BaseModel):
    trigger_id: str
    uuid: str | None = None
    confidence: float = 0.0
    is_new_visitor: bool = True
    scene_context: dict = {}
    screen_id: str = "screen_0"


@app.post("/trigger")
async def trigger_endpoint(body: TriggerPayload):
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

    try:
        video_path = await assemble(
            selection.base_video, [(audio_path, _INSERT_MIN_OFFSET_MS)], body.trigger_id
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


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await app.state.ws.connect(ws)
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
