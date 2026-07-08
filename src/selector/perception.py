"""Pure scene_context -> selection-signal helpers (TODO-7).

Every perception key is optional enrichment (part-1 spec): absent/empty
scene_context yields empty signals, and rank() with empty signals is the
identity ordering -- today's behavior is preserved."""
import json
import os
from dataclasses import dataclass

_MOOD_MIN_CONFIDENCE = float(os.getenv("MOOD_MIN_CONFIDENCE", "0.5"))
_OBJECT_MIN_CONFIDENCE = float(os.getenv("OBJECT_MIN_CONFIDENCE", "0.5"))
# 'person' rides in virtually every triggering frame; matching it would make
# every tagged ad "object-matched" and destroy the ranking signal.
_IGNORED_LABELS = {"person"}


@dataclass(frozen=True)
class Signals:
    mood: str | None = None
    labels: frozenset = frozenset()

    @property
    def empty(self) -> bool:
        return self.mood is None and not self.labels


def extract_signals(scene_context) -> Signals:
    if not isinstance(scene_context, dict) or not scene_context:
        return Signals()
    viewer = scene_context.get("viewer")
    viewer = viewer if isinstance(viewer, dict) else {}
    mood = viewer.get("mood")
    conf = viewer.get("mood_confidence")
    try:
        if mood is not None and conf is not None and float(conf) < _MOOD_MIN_CONFIDENCE:
            mood = None
    except (TypeError, ValueError):
        mood = None
    labels = set()
    objects = scene_context.get("objects")
    for o in (objects if isinstance(objects, list) else []):
        if not isinstance(o, dict) or not o.get("label"):
            continue
        label = str(o["label"]).lower()
        try:
            confident = float(o.get("confidence", 1.0)) >= _OBJECT_MIN_CONFIDENCE
        except (TypeError, ValueError):
            confident = False
        if confident and label not in _IGNORED_LABELS:
            labels.add(label)
    return Signals(mood=str(mood).lower() if mood else None, labels=frozenset(labels))


def ad_targeting(row) -> dict | None:
    """ads.targeting as a dict (asyncpg returns jsonb as str without a codec)."""
    raw = row["targeting"]
    if raw is None:
        return None
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    return parsed if isinstance(parsed, dict) else None


def score_ad(targeting: dict | None, signals: Signals) -> int:
    if not targeting or signals.empty:
        return 0
    score = 0
    if signals.mood and signals.mood in {str(m).lower() for m in targeting.get("moods") or []}:
        score += 2
    if signals.labels & {str(o).lower() for o in targeting.get("objects") or []}:
        score += 1
    return score


def rank(rows, signals: Signals) -> list:
    """Stable re-rank of already-eligible ad rows: highest score first, ties keep
    the incoming SQL order. Never filters -- enrichment only re-ranks (decision 2)."""
    return sorted(rows, key=lambda r: -score_ad(ad_targeting(r), signals))


def decision_factors(signals: Signals, score: int) -> dict | None:
    if score <= 0:
        return None
    return {"perception": {"mood": signals.mood,
                           "objects": sorted(signals.labels),
                           "match_score": score}}
