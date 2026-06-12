import json
from unittest.mock import AsyncMock

from src.selector.selector import select_variants


def _identity_row(name="Jason", blocked=False):
    return {"name": name, "is_blocked": blocked}


def _ad_row(slug, base="/assets/standard.mp4"):
    return {
        "base_video": base,
        "slug": slug,
        "default_props": json.dumps({"color": "#fff"}),
        "personalized_field": "text",
    }


def _db(identity=None, ad_rows=()):
    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=lambda q, *a: (
        identity if "identities" in q else (dict(ad_rows[0]) if ad_rows else None)
    ))
    db.fetch = AsyncMock(return_value=[dict(r) for r in ad_rows])
    return db


def _trigger(uuid="u1", new=False):
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
    calls = {"identities": 0}
    ads = [_ad_row("neon")]

    async def fetchrow(q, *a):
        if "identities" in q:
            calls["identities"] += 1
            return _identity_row() if calls["identities"] == 1 else None
        return dict(ads[0])

    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=fetchrow)
    db.fetch = AsyncMock(return_value=[dict(r) for r in ads])

    out = await select_variants(_trigger(), db, count=2)
    assert len(out) == 2 and all(s.type == "personalized" for s in out)


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
