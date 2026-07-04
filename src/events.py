"""Single append-only emitter for the God View `events` journal (CQRS: services
only append; the projector folds events into the summary tables).

Each payload carries a `screen_kind` discriminator plus the raw `screen_id` so the
projector's scope resolver picks the right registry and resolves org/location/
system. Pre-display (render-lane) events carry the TRIGGER's camera screen_id via
`camera_scope()` (resolved against the cameras registry); events that know the
concrete display carry `display_scope()` (resolved against the displays registry).
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def display_scope(screen_id):
    """Standard screen fields for a display-side event (the concrete display is
    known). Resolved by the projector against the displays registry.

    screen_id may be None; the projector treats an absent/unresolved screen_id as
    null scope (never crashes).
    """
    return {"screen_id": screen_id, "screen_kind": "display"}


def camera_scope(screen_id):
    """Screen fields for a pre-display (render-lane) event, which has no concrete
    display yet. Carries the TRIGGER's camera screen_id so the projector resolves
    system/location/org from the cameras registry — without this, decision and
    composition rows would land permanently unscoped (screen_id=None).
    """
    return {"screen_id": screen_id, "screen_kind": "camera"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def emit(db, trigger_id: str, event_type: str, status: str, payload: dict) -> None:
    """Append one row to `events`. Never raises into the caller (a logging failure
    must not sink the render/playback path)."""
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
