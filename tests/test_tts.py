from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tts.gateway import synthesize, _cache_key


def _resp(content: bytes) -> MagicMock:
    r = MagicMock()
    r.content = content
    r.raise_for_status = MagicMock()
    return r


async def test_cache_hit_returns_cached_path_no_http(tmp_path):
    key = _cache_key("uuid-1", "v1", "Hello Alice")
    cached = tmp_path / f"{key}.mp3"
    cached.write_bytes(b"cached-audio")

    http = AsyncMock()
    with patch("src.tts.gateway._CACHE_DIR", tmp_path):
        result = await synthesize("Hello Alice", "uuid-1", "v1", http)

    assert result == cached
    http.post.assert_not_called()


async def test_cache_miss_calls_elevenlabs_and_stores_file(tmp_path):
    http = AsyncMock()
    http.post = AsyncMock(return_value=_resp(b"el-audio"))

    with patch("src.tts.gateway._CACHE_DIR", tmp_path), \
         patch("src.tts.gateway._EL_API_KEY", "test-key"):
        result = await synthesize("Hello Bob", "uuid-2", "v1", http)

    assert result is not None
    assert result.read_bytes() == b"el-audio"
    http.post.assert_called_once()
    assert "elevenlabs" in http.post.call_args[0][0]


async def test_elevenlabs_fail_falls_back_to_misoone(tmp_path):
    fail_resp = MagicMock()
    fail_resp.raise_for_status = MagicMock(side_effect=Exception("EL 500"))
    success_resp = _resp(b"miso-audio")

    http = AsyncMock()
    http.post = AsyncMock(side_effect=[fail_resp, success_resp])

    with patch("src.tts.gateway._CACHE_DIR", tmp_path), \
         patch("src.tts.gateway._EL_API_KEY", "el-key"), \
         patch("src.tts.gateway._MISO_KEY", "miso-key"):
        result = await synthesize("Hello Charlie", "uuid-3", "v1", http)

    assert result is not None
    assert result.read_bytes() == b"miso-audio"
    assert http.post.call_count == 2


async def test_all_providers_fail_returns_none(tmp_path):
    http = AsyncMock()
    http.post = AsyncMock(side_effect=Exception("network down"))

    with patch("src.tts.gateway._CACHE_DIR", tmp_path), \
         patch("src.tts.gateway._EL_API_KEY", "el-key"), \
         patch("src.tts.gateway._MISO_KEY", "miso-key"):
        result = await synthesize("Hello Dave", "uuid-4", "v1", http)

    assert result is None


async def test_different_text_produces_different_cache_key():
    k1 = _cache_key("uuid-1", "v1", "Hello Alice")
    k2 = _cache_key("uuid-1", "v1", "Hello Bob")
    assert k1 != k2


async def test_same_inputs_produce_same_cache_key():
    k1 = _cache_key("uuid-1", "v1", "Hello Alice")
    k2 = _cache_key("uuid-1", "v1", "Hello Alice")
    assert k1 == k2
