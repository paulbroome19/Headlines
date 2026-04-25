"""
TTS client abstraction. Supports elevenlabs and openai providers.
Uses stdlib urllib — no new dependencies.

Configuration (environment variables):
  TTS_PROVIDER         elevenlabs | openai   (default: elevenlabs)
  TTS_VOICE            voice id / name       (default: JBFqnCBsd6RMkjVDRZzb — ElevenLabs "George", British male)
  TTS_MODEL            model id              (default: eleven_turbo_v2_5)
  TTS_AUDIO_FORMAT     format string         (default: mp3_44100_128)
  ELEVENLABS_API_KEY   required when provider=elevenlabs
  OPENAI_API_KEY       required when provider=openai
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request

from core.platform.config.settings import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60

_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
_OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"

# Defaults
_DEFAULT_PROVIDER = "elevenlabs"
_DEFAULT_VOICE_ELEVENLABS = "JBFqnCBsd6RMkjVDRZzb"   # George — British male, professional
_DEFAULT_VOICE_OPENAI = "onyx"
_DEFAULT_MODEL_ELEVENLABS = "eleven_turbo_v2_5"
_DEFAULT_MODEL_OPENAI = "tts-1"
_DEFAULT_FORMAT_ELEVENLABS = "mp3_44100_128"
_DEFAULT_FORMAT_OPENAI = "mp3"


def tts_provider() -> str:
    return os.environ.get("TTS_PROVIDER", _DEFAULT_PROVIDER).lower()


def tts_voice() -> str:
    provider = tts_provider()
    default = _DEFAULT_VOICE_ELEVENLABS if provider == "elevenlabs" else _DEFAULT_VOICE_OPENAI
    return os.environ.get("TTS_VOICE", default)


def tts_model() -> str:
    provider = tts_provider()
    default = _DEFAULT_MODEL_ELEVENLABS if provider == "elevenlabs" else _DEFAULT_MODEL_OPENAI
    return os.environ.get("TTS_MODEL", default)


def tts_audio_format() -> str:
    provider = tts_provider()
    default = _DEFAULT_FORMAT_ELEVENLABS if provider == "elevenlabs" else _DEFAULT_FORMAT_OPENAI
    return os.environ.get("TTS_AUDIO_FORMAT", default)


def ext_from_format(audio_format: str) -> str:
    """Return file extension for a given audio_format string."""
    fmt = audio_format.lower()
    if fmt.startswith("mp3") or fmt == "mp3":
        return "mp3"
    if fmt.startswith("pcm"):
        return "pcm"
    if fmt == "opus":
        return "opus"
    if fmt.startswith("ulaw") or fmt.startswith("alaw"):
        return "wav"
    return "mp3"


def compute_script_hash(script: str) -> str:
    """16-char SHA-256 hex of the bulletin script. Used as cache key."""
    return hashlib.sha256(script.encode()).hexdigest()[:16]


def synthesize(
    text: str,
    *,
    provider: str,
    voice: str,
    model: str,
    audio_format: str,
) -> bytes | None:
    """
    Call the TTS provider and return raw audio bytes, or None on failure.
    Fail closed: returns None if the API key is absent or the call fails.
    """
    if provider == "elevenlabs":
        return _elevenlabs(text, voice=voice, model=model, audio_format=audio_format)
    if provider == "openai":
        return _openai(text, voice=voice, model=model)
    logger.warning("tts_client: unknown provider %r — skipping", provider)
    return None


# ── ElevenLabs ────────────────────────────────────────────────────────────────

def _elevenlabs(text: str, *, voice: str, model: str, audio_format: str) -> bytes | None:
    api_key = settings.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        logger.warning("tts_client: ELEVENLABS_API_KEY not set — skipping TTS")
        return None

    url = _ELEVENLABS_TTS_URL.format(voice_id=voice)
    # output_format as query param (ElevenLabs recommends this)
    url = f"{url}?output_format={audio_format}"
    body = json.dumps({
        "text": text,
        "model_id": model,
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            audio_bytes = resp.read()
    except urllib.error.HTTPError as e:
        body_snippet = e.read(512).decode(errors="replace")
        logger.warning("tts_client ElevenLabs HTTP %s: %s — %s", e.code, e.reason, body_snippet)
        return None
    except Exception as e:
        logger.warning("tts_client ElevenLabs error: %s", e)
        return None

    if not audio_bytes:
        logger.warning("tts_client ElevenLabs returned empty response")
        return None

    logger.info("tts_client ElevenLabs OK  bytes=%d  voice=%s  model=%s", len(audio_bytes), voice, model)
    return audio_bytes


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _openai(text: str, *, voice: str, model: str) -> bytes | None:
    api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("tts_client: OPENAI_API_KEY not set — skipping TTS")
        return None

    body = json.dumps({
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": "mp3",
    }).encode()

    req = urllib.request.Request(
        _OPENAI_TTS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            audio_bytes = resp.read()
    except urllib.error.HTTPError as e:
        body_snippet = e.read(512).decode(errors="replace")
        logger.warning("tts_client OpenAI HTTP %s: %s — %s", e.code, e.reason, body_snippet)
        return None
    except Exception as e:
        logger.warning("tts_client OpenAI error: %s", e)
        return None

    if not audio_bytes:
        logger.warning("tts_client OpenAI returned empty response")
        return None

    logger.info("tts_client OpenAI OK  bytes=%d  voice=%s  model=%s", len(audio_bytes), voice, model)
    return audio_bytes
