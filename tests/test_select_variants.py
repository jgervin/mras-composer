import json
from unittest.mock import AsyncMock

from src.selector.selector import select_variants


def _identity_row(name="Jason", blocked=False):
    return {"name": name, "is_blocked": blocked}


def _ad_row(slug, base="/assets/standard.mp4"):
    return {
        "ad_id": f"ad-{slug}",
        "component_id": f"comp-{slug}",
        "base_video": base,
        "slug": slug,
        "default_props": json.dumps({"color": "#fff"}),
        "personalized_field": "text",
    }


def _db(identity=None, ad_rows=()):
    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=lambda q, *a: (
        identity if "subject_profiles" in q else (dict(ad_rows[0]) if ad_rows else None)
    ))
    db.fetch = AsyncMock(return_value=[dict(r) for r in ad_rows])
    return db


def _trigger(uuid="11111111-1111-1111-1111-111111111111", new=False):
    return {"uuid": uuid, "is_new_visitor": new, "screen_id": "screen_0"}


async def test_new_visitor_yields_a_single_standard_selection():
    out = await select_variants(_trigger(uuid=None, new=True), _db(), count=4)
    assert len(out) == 1 and out[0].type == "standard"


async def test_distinct_ads_map_to_distinct_variants():
    db = _db(_identity_row(), [_ad_row("neon"), _ad_row("fish"), _ad_row("snow")])
    out = await select_variants(_trigger(), db, count=3)
    assert [s.composition_id for s in out] == ["comp-neon", "comp-fish", "comp-snow"]
    assert all(s.overlay_props["text"] == "Jason" for s in out)
    assert all(s.tts_text for s in out)


async def test_fewer_ads_than_displays_cycles_them():
    db = _db(_identity_row(), [_ad_row("neon"), _ad_row("fish")])
    out = await select_variants(_trigger(), db, count=4)
    assert [s.composition_id for s in out] == [
        "comp-neon", "comp-fish", "comp-neon", "comp-fish",
    ]


async def test_no_active_ads_falls_back_to_one_legacy_selection_per_display():
    db = _db(_identity_row(), [])
    out = await select_variants(_trigger(), db, count=4)
    assert len(out) == 4
    assert all(s.type == "personalized" and s.composition_id is None for s in out)


async def test_blocked_person_gets_standard():
    db = _db(_identity_row(blocked=True), [_ad_row("neon")])
    out = await select_variants(_trigger(), db, count=2)
    assert len(out) == 1 and out[0].type == "standard"


async def test_identity_deleted_mid_flight_falls_back_instead_of_crashing():
    """select() saw the person, but they vanish before the variants query's
    second identity lookup — must degrade to the legacy selection, not 500."""
    calls = {"subject_profiles": 0}
    ads = [_ad_row("neon")]

    async def fetchrow(q, *a):
        if "subject_profiles" in q:
            calls["subject_profiles"] += 1
            return _identity_row() if calls["subject_profiles"] == 1 else None
        return dict(ads[0])

    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=fetchrow)
    db.fetch = AsyncMock(return_value=[dict(r) for r in ads])

    out = await select_variants(_trigger(), db, count=2)
    assert len(out) == 2 and all(s.type == "personalized" for s in out)


async def test_name_nulled_mid_flight_falls_back_instead_of_personalizing_none():
    """Same mid-flight degradation as the deleted case, but the re-fetch
    returns a row whose display_name went NULL (e.g. profile merged) —
    must fall back to [base] * count, never format "Welcome, None!"."""
    calls = {"subject_profiles": 0}
    ads = [_ad_row("neon")]

    async def fetchrow(q, *a):
        if "subject_profiles" in q:
            calls["subject_profiles"] += 1
            return _identity_row() if calls["subject_profiles"] == 1 else _identity_row(name=None)
        return dict(ads[0])

    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=fetchrow)
    db.fetch = AsyncMock(return_value=[dict(r) for r in ads])

    out = await select_variants(_trigger(), db, count=2)
    assert len(out) == 2
    assert all(s.person_name == "Jason" for s in out)  # base selection, not the NULL re-fetch
    assert all(s.overlay_props["text"] == "Jason" for s in out)


async def test_refetch_query_filters_on_known_status():
    """The variants re-fetch must carry the same status predicate as
    select() — merged/deleted profiles must not personalize."""
    db = _db(_identity_row(), [_ad_row("neon")])
    await select_variants(_trigger(), db, count=2)
    refetch_query = db.fetchrow.call_args_list[-1].args[0]
    assert "subject_profiles" in refetch_query
    assert "status = 'known'" in refetch_query


async def test_variant_ads_are_ordered_randomly_not_by_recency():
    """Newest-first selection deterministically served the two newest
    (decorative, no-text) ads to every 2-display person — name spoken,
    never written. Order must be random per trigger."""
    db = _db(_identity_row(), [_ad_row("neon")])
    await select_variants(_trigger(), db, count=2)
    query = db.fetch.call_args.args[0]
    assert "random()" in query.lower()
    assert "created_at desc" not in query.lower()


async def test_selections_carry_person_name_for_kiosk_debug():
    db = _db(_identity_row(name="Ragnar"), [_ad_row("neon")])
    out = await select_variants(_trigger(), db, count=1)
    assert out[0].person_name == "Ragnar"


# ---------------------------------------------------------------------------
# TODO-7: perception-ranked variant ordering (targeting_supported=True).
# select() runs inside select_variants(), so with the flag on BOTH ad lookups
# go through db.fetch — the dispatcher below routes on the SQL itself.
# ---------------------------------------------------------------------------

_SCENE_SAD = {"viewer": {"mood": "sad", "mood_confidence": 0.9},
              "objects": [], "faces_tracked": 1}


def _targeted_ad_row(slug, targeting=None):
    return {**_ad_row(slug), "targeting": targeting}


def _db_targeting(variant_rows, identity=None):
    """fetch dispatcher: select()'s candidate query (created_at DESC) vs the
    variants query (random()); fetchrow serves both identity lookups."""
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=identity or _identity_row())

    async def fetch(q, *a):
        rows = [dict(r) for r in variant_rows]
        if "random()" in q:
            return rows
        assert "created_at DESC" in q, f"unexpected fetch SQL: {q}"
        return rows  # candidate pool: same catalog, newest-first is irrelevant here

    db.fetch = AsyncMock(side_effect=fetch)
    return db


def _trigger_sad():
    return {**_trigger(), "scene_context": _SCENE_SAD}


async def test_matching_variant_moves_to_display_1():
    db = _db_targeting([_targeted_ad_row("neon"),
                        _targeted_ad_row("fish", {"moods": ["sad"]})])
    out = await select_variants(_trigger_sad(), db, count=2, targeting_supported=True)
    assert [s.composition_id for s in out] == ["comp-fish", "comp-neon"]


async def test_empty_signals_preserve_incoming_variant_order():
    db = _db_targeting([_targeted_ad_row("neon"),
                        _targeted_ad_row("fish", {"moods": ["sad"]})])
    out = await select_variants({**_trigger(), "scene_context": {}}, db,
                                count=2, targeting_supported=True)
    assert [s.composition_id for s in out] == ["comp-neon", "comp-fish"]


async def test_targeting_variants_query_selects_targeting_column():
    db = _db_targeting([_targeted_ad_row("neon")])
    await select_variants(_trigger_sad(), db, count=2, targeting_supported=True)
    variant_query = next(c.args[0] for c in db.fetch.call_args_list
                         if "random()" in c.args[0])
    assert "a.targeting" in variant_query


async def test_legacy_variants_query_never_references_targeting():
    # I2 deploy safety: default flag => today's SQL, safe on unmigrated DBs.
    db = _db(_identity_row(), [_ad_row("neon")])
    await select_variants(_trigger(), db, count=2)
    assert "targeting" not in db.fetch.call_args.args[0]
