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
    return settings.tts_provider.lower()


def tts_voice() -> str:
    return settings.tts_voice


def tts_model() -> str:
    return settings.tts_model


def tts_audio_format() -> str:
    return settings.tts_audio_format


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


def mp3_duration(path: str) -> float | None:
    """
    Return MP3 duration in seconds by scanning frame headers.
    Uses only stdlib — no mutagen dependency.
    Returns None if the file cannot be read or parsed.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    # Skip ID3v2 tag if present
    offset = 0
    if data[:3] == b"ID3":
        tag_size = (
            ((data[6] & 0x7F) << 21) |
            ((data[7] & 0x7F) << 14) |
            ((data[8] & 0x7F) << 7)  |
             (data[9] & 0x7F)
        )
        offset = 10 + tag_size

    # MPEG1 Layer3 sample rate and bitrate tables
    _sample_rates = {0: 44100, 1: 48000, 2: 32000}
    _bitrates = {
        1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96,
        8: 112, 9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320,
    }

    total_samples = 0
    sample_rate = None
    n = len(data)

    while offset < n - 4:
        b = data[offset: offset + 4]
        if b[0] == 0xFF and (b[1] & 0xE0) == 0xE0:
            mpeg_version = (b[1] >> 3) & 0x3
            layer        = (b[1] >> 1) & 0x3
            bitrate_idx  = (b[2] >> 4) & 0xF
            sr_idx       = (b[2] >> 2) & 0x3
            padding      = (b[2] >> 1) & 0x1
            if mpeg_version == 3 and layer == 1 and bitrate_idx in _bitrates:
                sr = _sample_rates.get(sr_idx)
                if sr:
                    sample_rate = sr
                    br = _bitrates[bitrate_idx] * 1000
                    frame_size = 144 * br // sr + padding
                    total_samples += 1152
                    offset += max(frame_size, 1)
                    continue
        offset += 1

    if sample_rate and total_samples > 0:
        return total_samples / sample_rate
    return None


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
    api_key = settings.elevenlabs_api_key
    logger.debug("tts_client ElevenLabs: api_key present=%s  voice=%s  model=%s", bool(api_key), voice, model)
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
        if e.code == 401:
            logger.warning("tts_client ElevenLabs 401 Unauthorized — check ELEVENLABS_API_KEY: %s", body_snippet)
        elif e.code == 402:
            logger.warning("tts_client ElevenLabs 402 Payment Required — voice '%s' may require a paid plan: %s", voice, body_snippet)
        else:
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
    api_key = settings.openai_api_key
    logger.debug("tts_client OpenAI: api_key present=%s  voice=%s  model=%s", bool(api_key), voice, model)
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
