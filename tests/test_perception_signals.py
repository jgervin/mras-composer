"""Pure scene_context -> selection-signal helpers (TODO-7).

Contract: every perception key is optional enrichment (part-1 spec). Absent or
empty scene_context yields empty Signals, and rank() with empty signals is the
identity ordering — today's selection behavior is preserved byte-for-byte."""
import json

from src.selector.perception import (
    Signals,
    ad_targeting,
    decision_factors,
    extract_signals,
    rank,
    score_ad,
)

# Real payload shape sampled from the dev DB (events, event_type='detection').
_REAL_SCENE_CONTEXT = {
    "viewer": {"mood": "sad", "track_id": "t-6", "attending": True,
               "evidence_frames": 22, "mood_confidence": 0.72},
    "objects": [{"bbox": [394, 363, 1077, 715], "color": "black",
                 "label": "person", "source": "yolo11n", "confidence": 0.82}],
    "faces_tracked": 1,
}


# ---------------------------------------------------------------------------
# extract_signals
# ---------------------------------------------------------------------------


def test_real_payload_extracts_mood_and_excludes_person_label():
    sig = extract_signals(_REAL_SCENE_CONTEXT)
    assert sig == Signals(mood="sad", labels=frozenset())
    assert not sig.empty


def test_empty_and_absent_scene_context_yield_empty_signals():
    assert extract_signals({}).empty
    assert extract_signals(None).empty
    assert extract_signals({"faces_tracked": 1}).empty  # viewer missing


def test_low_mood_confidence_drops_mood():
    sig = extract_signals({"viewer": {"mood": "sad", "mood_confidence": 0.3}})
    assert sig.mood is None


def test_low_object_confidence_drops_label():
    sig = extract_signals({"objects": [{"label": "backpack", "confidence": 0.2}]})
    assert sig.labels == frozenset()


def test_confident_object_label_is_extracted_lowercased():
    sig = extract_signals({"objects": [{"label": "Backpack", "confidence": 0.9}]})
    assert sig.labels == frozenset({"backpack"})


def test_garbage_scene_context_never_raises():
    for garbage in ("nope", 42, [], {"viewer": "x", "objects": "y"},
                    {"viewer": {"mood": "sad", "mood_confidence": "high"}},
                    {"objects": [None, {}, {"label": "cup", "confidence": "big"}]}):
        sig = extract_signals(garbage)
        assert isinstance(sig, Signals)


# ---------------------------------------------------------------------------
# ad_targeting / score_ad
# ---------------------------------------------------------------------------


def _row(targeting):
    return {"targeting": targeting}


def test_ad_targeting_parses_jsonb_str_and_dict_and_null():
    assert ad_targeting(_row(None)) is None
    assert ad_targeting(_row({"moods": ["sad"]})) == {"moods": ["sad"]}
    assert ad_targeting(_row(json.dumps({"moods": ["sad"]}))) == {"moods": ["sad"]}
    assert ad_targeting(_row(json.dumps(["not", "a", "dict"]))) is None


def test_score_null_targeting_is_zero():
    assert score_ad(None, Signals(mood="sad")) == 0


def test_score_empty_signals_is_zero():
    assert score_ad({"moods": ["sad"]}, Signals()) == 0


def test_score_mood_match_is_two():
    assert score_ad({"moods": ["sad"]}, Signals(mood="sad")) == 2


def test_score_object_match_is_one():
    assert score_ad({"objects": ["backpack"]},
                    Signals(labels=frozenset({"backpack"}))) == 1


def test_score_mood_and_object_match_is_three():
    sig = Signals(mood="happy", labels=frozenset({"bottle"}))
    assert score_ad({"moods": ["happy"], "objects": ["bottle"]}, sig) == 3


def test_score_garbage_targeting_never_raises():
    sig = Signals(mood="sad", labels=frozenset({"cup"}))
    assert score_ad({"moods": None, "objects": None}, sig) == 0
    assert score_ad({"moods": [1, 2], "objects": [None]}, sig) == 0


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


def test_rank_with_all_zero_scores_preserves_input_order_exactly():
    rows = [_row(None), _row(None), _row({"moods": ["happy"]})]
    assert rank(rows, Signals(mood="sad")) == rows          # no match anywhere
    assert rank(rows, Signals()) == rows                     # empty signals


def test_rank_moves_matching_row_first_and_keeps_tie_order():
    a, b, c = _row(None), _row({"moods": ["sad"]}), _row(None)
    assert rank([a, b, c], Signals(mood="sad")) == [b, a, c]


def test_rank_never_filters():
    rows = [_row(None), _row({"moods": ["sad"]})]
    assert len(rank(rows, Signals(mood="sad"))) == 2


# ---------------------------------------------------------------------------
# decision_factors
# ---------------------------------------------------------------------------


def test_decision_factors_none_when_no_match():
    assert decision_factors(Signals(mood="sad"), 0) is None


def test_decision_factors_reports_match():
    sig = Signals(mood="sad", labels=frozenset({"cup", "bottle"}))
    assert decision_factors(sig, 3) == {
        "perception": {"mood": "sad", "objects": ["bottle", "cup"], "match_score": 3}
    }
