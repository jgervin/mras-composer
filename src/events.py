"""Single append-only emitter for the God View `events` journal (CQRS: services
only append; the projector folds events into the summary tables).

Every composer/display-side payload carries `screen_kind='display'` plus the raw
`screen_id` so the projector's scope resolver can pick the displays registry and
resolve org/location/system. Use `display_scope()` to stamp both consistently.
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def display_scope(screen_id):
    """Standard screen fields for every composer/display-side event.

    screen_id may be None (render lane is display-agnostic); the projector treats
    an absent/unresolved screen_id as null scope (never crashes).
    """
    return {"screen_id": screen_id, "screen_kind": "display"}


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
