from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.selector.selector import select, AdSelection

_FAKE_VIDEO = Path("/fake/standard.mp4")


def _db(name: str = "Alice", is_blocked: bool = False, found: bool = True) -> AsyncMock:
    db = AsyncMock()
    identity_row = {"name": name, "is_blocked": is_blocked} if found else None
    # Second call is the ad query; default None → no active ad → M3 overlay_text fallback.
    db.fetchrow = AsyncMock(side_effect=[identity_row, None])
    return db


async def test_new_visitor_returns_standard_without_db_query():
    db = _db()
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": None, "is_new_visitor": True}, db)
    assert result.type == "standard"
    db.fetchrow.assert_not_called()


async def test_uuid_with_is_new_visitor_true_returns_standard():
    db = _db()
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": "some-uuid", "is_new_visitor": True}, db)
    assert result.type == "standard"
    db.fetchrow.assert_not_called()


async def test_known_unblocked_visitor_returns_personalized():
    db = _db(name="Alice", is_blocked=False)
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": "uuid-abc", "is_new_visitor": False}, db)
    assert result.type == "personalized"
    assert result.person_uuid == "uuid-abc"
    assert "Alice" in result.tts_text


async def test_blocklisted_uuid_returns_standard():
    db = _db(name="Alice", is_blocked=True)
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": "uuid-abc", "is_new_visitor": False}, db)
    assert result.type == "standard"


async def test_uuid_not_in_db_returns_standard():
    db = _db(found=False)
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": "unknown", "is_new_visitor": False}, db)
    assert result.type == "standard"


async def test_tts_text_uses_person_name():
    db = _db(name="Jason")
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO), \
         patch("src.selector.selector._TTS_TEMPLATE", "Hey {name}, welcome!"):
        result = await select({"uuid": "uuid-xyz", "is_new_visitor": False}, db)
    assert result.tts_text == "Hey Jason, welcome!"


async def test_personalized_selection_sets_overlay_text_from_name():
    db = _db(name="Jason")
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO), \
         patch("src.selector.selector._OVERLAY_TEMPLATE", "{name}"):
        result = await select({"uuid": "uuid-xyz", "is_new_visitor": False}, db)
    assert result.overlay_text == "Jason"


async def test_standard_selection_has_no_overlay_text():
    db = _db()
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": None, "is_new_visitor": True}, db)
    assert result.overlay_text is None


async def test_identified_visitor_gets_active_custom_ad():
    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=[
        {"name": "Jason", "is_blocked": False},
        {"ad_id": "ad-neon", "component_id": "comp-neon",
         "base_video": "/assets/standard.mp4", "slug": "neon",
         "default_props": {"color": "#ff2d2d"}, "personalized_field": "text"},
    ])
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": "uuid-abc", "is_new_visitor": False}, db)
    assert result.type == "personalized"
    assert result.composition_id == "comp-neon"
    assert result.overlay_props["text"] == "Jason"
    assert result.overlay_props["color"] == "#ff2d2d"


# ---------------------------------------------------------------------------
# God View schema migration: subject_profiles replaces identities
# RED before fix (queries identities → exception); GREEN after fix.
# ---------------------------------------------------------------------------

_SUBJECT_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


async def test_subject_profile_personalized_text_overlay_no_ads():
    """Known subject_profiles row + no ads → text-overlay fallback, personalized.
    RED: current selector queries identities (deleted table) → UndefinedTableError.
    GREEN: after fix queries subject_profiles → overlay_text and person_name set."""

    async def _fetchrow(q, *args):
        if "identities" in q:
            raise Exception('relation "identities" does not exist')
        if "subject_profiles" in q:
            # Simulates: id=_SUBJECT_UUID, display_name='Jason Ervin', status='known'
            return {"name": "Jason Ervin", "is_blocked": False}
        return None  # ads query → None → text-overlay fallback path (lines 68-75)

    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=_fetchrow)

    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO), \
         patch("src.selector.selector._OVERLAY_TEMPLATE", "{name}"):
        result = await select({"uuid": _SUBJECT_UUID, "is_new_visitor": False}, db)

    assert result.type == "personalized"
    assert result.overlay_text == "Jason Ervin"
    assert result.person_name == "Jason Ervin"


async def test_new_visitor_flag_returns_standard_regardless_of_subject_profile():
    """is_new_visitor=True → standard; fetchrow never called (no schema involved)."""
    db = AsyncMock()
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": _SUBJECT_UUID, "is_new_visitor": True}, db)
    assert result.type == "standard"
    db.fetchrow.assert_not_called()


async def test_unknown_subject_profile_id_returns_standard():
    """subject_profiles row not found → standard.
    RED: current selector queries identities → exception.
    GREEN: after fix queries subject_profiles → None → standard."""

    async def _fetchrow(q, *args):
        if "identities" in q:
            raise Exception('relation "identities" does not exist')
        return None  # subject_profiles: id not found

    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=_fetchrow)

    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": "no-such-profile-id", "is_new_visitor": False}, db)
    assert result.type == "standard"
