from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.orchestrator.model import Round
from src.orchestrator.renderer import Renderer
from src.selector.selector import AdSelection


def _sel(name="Jason"):
    return AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                       tts_text=f"Welcome, {name}!", person_uuid="jason",
                       overlay_text=name, person_name=name)


async def test_round2_renders_two_variants_in_order():
    db, http = AsyncMock(), AsyncMock()
    compose = AsyncMock(side_effect=[Path("/tmp/x-0.mp4"), Path("/tmp/x-1.mp4")])
    url = lambda p: f"http://c/media/{p.name}"
    r = Renderer(db, http, compose=compose, url_for=url,
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    with patch("src.orchestrator.renderer.select_variants",
               AsyncMock(return_value=[_sel(), _sel()])):
        urls = await r.render("jason", Round.ROUND2)
    assert urls == ["http://c/media/x-0.mp4", "http://c/media/x-1.mp4"]
    assert compose.await_count == 2


async def test_opener_renders_single_variant():
    db, http = AsyncMock(), AsyncMock()
    compose = AsyncMock(return_value=Path("/tmp/op.mp4"))
    r = Renderer(db, http, compose=compose, url_for=lambda p: f"u/{p.name}",
                 synthesize=AsyncMock(return_value=Path("/tmp/a.wav")))
    with patch("src.orchestrator.renderer.select", AsyncMock(return_value=_sel())):
        urls = await r.render("jason", Round.OPENER)
    assert urls == ["u/op.mp4"]
    assert compose.await_count == 1
