import time


class SceneContextCache:
    """subject -> scene_context handoff from /trigger to render-time selection.

    The orchestrated path re-selects inside Renderer.render() with a synthetic
    trigger; this cache carries the triggering frame's perception across that
    gap. TTL-bounded so a stale mood can't follow a person around. Per-process
    (fine under the single-process assumption); a cross-worker miss degrades
    to {} — i.e. today's unenriched selection (TODO-7 M3)."""

    def __init__(self, ttl_s: float = 120.0, clock=time.monotonic):
        self._ttl, self._clock = ttl_s, clock
        self._items: dict[str, tuple[float, dict]] = {}

    def put(self, subject: str, scene_context: dict) -> None:
        if not subject:
            return
        now = self._clock()
        # Opportunistic prune (I1): drop expired entries so one-time subjects
        # can't accumulate forever in a long-lived process.
        expired = [k for k, (ts, _) in self._items.items() if now - ts > self._ttl]
        for k in expired:
            del self._items[k]
        self._items[subject] = (now, scene_context or {})

    def get(self, subject: str) -> dict:
        item = self._items.get(subject)
        if item is None:
            return {}
        ts, ctx = item
        if self._clock() - ts > self._ttl:
            self._items.pop(subject, None)
            return {}
        return ctx
