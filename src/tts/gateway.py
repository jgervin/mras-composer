import hashlib
import os
from pathlib import Path

import httpx

_CACHE_DIR = Path(os.getenv("TTS_CACHE_DIR", "/tmp/tts_cache"))
_EL_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
_MISO_KEY = os.getenv("MISOONE_API_KEY", "")
_EL_BASE = "https://api.elevenlabs.io/v1"
_MISO_BASE = os.getenv("MISOONE_BASE_URL", "https://api.misoone.com/v1")  # TODO: verify MisoOne endpoint


def _cache_key(person_uuid: str, voice_id: str, text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"{person_uuid}_{voice_id}_{h}"


async def synthesize(
    text: str,
    person_uuid: str,
    voice_id: str,
    http: httpx.AsyncClient,
) -> Path | None:
    key = _cache_key(person_uuid, voice_id, text)
    cached = _CACHE_DIR / f"{key}.mp3"
    if cached.exists():
        return cached

    audio = await _try_elevenlabs(text, voice_id, http)
    if audio is None:
        audio = await _try_misoone(text, http)

    if audio is None:
        return None

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(audio)
    return cached


async def _try_elevenlabs(text: str, voice_id: str, http: httpx.AsyncClient) -> bytes | None:
    if not _EL_API_KEY:
        return None
    try:
        resp = await http.post(
            f"{_EL_BASE}/text-to-speech/{voice_id}",
            headers={"xi-api-key": _EL_API_KEY},
            json={"text": text, "model_id": "eleven_turbo_v2"},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


async def _try_misoone(text: str, http: httpx.AsyncClient) -> bytes | None:
    if not _MISO_KEY:
        return None
    try:
        resp = await http.post(
            f"{_MISO_BASE}/synthesize",
            headers={"Authorization": f"Bearer {_MISO_KEY}"},
            json={"text": text},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None
