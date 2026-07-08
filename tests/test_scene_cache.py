"""SceneContextCache (TODO-7): subject -> scene_context handoff from /trigger
to the render-time re-selection inside Renderer.render(). TTL-bounded, and
put() opportunistically prunes expired entries (I1) so one-time subjects can't
accumulate forever."""
from src.selector.scene_cache import SceneContextCache

_CTX = {"viewer": {"mood": "sad", "mood_confidence": 0.9}}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_put_get_round_trip():
    cache = SceneContextCache()
    cache.put("subject-1", _CTX)
    assert cache.get("subject-1") == _CTX


def test_unknown_subject_returns_empty_dict():
    assert SceneContextCache().get("nobody") == {}


def test_ttl_expiry_returns_empty_and_evicts():
    clock = FakeClock()
    cache = SceneContextCache(ttl_s=120.0, clock=clock)
    cache.put("subject-1", _CTX)
    clock.now += 121.0
    assert cache.get("subject-1") == {}
    assert "subject-1" not in cache._items  # evicted, not just masked


def test_within_ttl_still_returns_context():
    clock = FakeClock()
    cache = SceneContextCache(ttl_s=120.0, clock=clock)
    cache.put("subject-1", _CTX)
    clock.now += 119.0
    assert cache.get("subject-1") == _CTX


def test_put_none_stores_empty_dict():
    cache = SceneContextCache()
    cache.put("subject-1", None)
    assert cache.get("subject-1") == {}


def test_falsy_subject_is_ignored():
    cache = SceneContextCache()
    cache.put("", _CTX)
    cache.put(None, _CTX)
    assert cache._items == {}


def test_put_prunes_expired_entries_of_other_subjects():
    # I1: a later put() for a DIFFERENT subject sweeps expired entries, so
    # one-time subjects can't accumulate forever in a long-lived process.
    clock = FakeClock()
    cache = SceneContextCache(ttl_s=120.0, clock=clock)
    cache.put("one-timer", _CTX)
    cache.put("fresh-ish", _CTX)
    clock.now += 121.0
    cache.put("newcomer", _CTX)
    assert "one-timer" not in cache._items
    assert "fresh-ish" not in cache._items
    assert cache.get("newcomer") == _CTX


def test_put_does_not_prune_live_entries():
    clock = FakeClock()
    cache = SceneContextCache(ttl_s=120.0, clock=clock)
    cache.put("alive", _CTX)
    clock.now += 60.0
    cache.put("newcomer", _CTX)
    assert cache.get("alive") == _CTX
