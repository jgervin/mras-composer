import base64
import hashlib
import io
import os
import wave
from pathlib import Path

import httpx

_CACHE_DIR = Path(os.getenv("TTS_CACHE_DIR", "/tmp/tts_cache"))
_EL_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_EL_BASE = "https://api.elevenlabs.io/v1"
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
_GEMINI_VOICE = os.getenv("GEMINI_VOICE", "Kore")
_GEMINI_SAMPLE_RATE = 24000


def _cache_key(person_uuid: str, voice_id: str, text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"{person_uuid}_{voice_id}_{h}"


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(_GEMINI_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


async def synthesize(
    text: str,
    person_uuid: str,
    voice_id: str,
    http: httpx.AsyncClient,
) -> Path | None:
    key = _cache_key(person_uuid, voice_id, text)
    for ext in (".mp3", ".wav"):
        p = _CACHE_DIR / f"{key}{ext}"
        if p.exists():
            return p

    audio = await _try_elevenlabs(text, voice_id, http)
    if audio is not None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _CACHE_DIR / f"{key}.mp3"
        p.write_bytes(audio)
        return p

    audio = await _try_gemini(text, http)
    if audio is not None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _CACHE_DIR / f"{key}.wav"
        p.write_bytes(audio)
        return p

    return None


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


async def _try_gemini(text: str, http: httpx.AsyncClient) -> bytes | None:
    if not _GEMINI_API_KEY:
        return None
    try:
        resp = await http.post(
            _GEMINI_BASE,
            headers={"x-goog-api-key": _GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": _GEMINI_VOICE}
                        }
                    },
                },
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        pcm_b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        return _pcm_to_wav(base64.b64decode(pcm_b64))
    except Exception:
        return None
