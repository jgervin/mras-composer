"""AdSelection must carry the ads.id and components.id UUIDs so the composer's
God View emissions (decision/made, composition/*, ad_run/*) can name real FK
targets (selected_ad_id, selected_creative_id / ad_id, component_id)."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.selector.selector import select

_FAKE_VIDEO = Path("/fake/standard.mp4")


async def test_custom_ad_selection_carries_ad_and_component_uuids():
    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=[
        {"name": "Jason", "is_blocked": False},
        {"ad_id": "ad-uuid-1", "component_id": "comp-uuid-9",
         "base_video": "/assets/standard.mp4", "slug": "neon",
         "default_props": {"color": "#ff2d2d"}, "personalized_field": "text"},
    ])
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": "uuid-abc", "is_new_visitor": False}, db)
    assert result.type == "personalized"
    assert result.ad_id == "ad-uuid-1"
    assert result.component_id == "comp-uuid-9"


async def test_text_fallback_selection_has_null_uuids():
    db = AsyncMock()
    db.fetchrow = AsyncMock(side_effect=[{"name": "Jason", "is_blocked": False}, None])
    with patch("src.selector.selector._STANDARD_VIDEO", _FAKE_VIDEO):
        result = await select({"uuid": "uuid-abc", "is_new_visitor": False}, db)
    assert result.type == "personalized"
    assert result.ad_id is None
    assert result.component_id is None
