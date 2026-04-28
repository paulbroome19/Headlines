"""
Voice discovery and registry.

Voices are fetched from the ElevenLabs API on first use and cached in memory
for 20 minutes. Only voices accessible to the configured API key are returned,
so library/paid voices unavailable to the account are never shown.

Fallback: if discovery fails, returns a single entry for the configured
default voice so the dropdown is never empty.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 20 * 60   # 20 minutes
_DISCOVERY_TIMEOUT = 10        # seconds

_cache_lock = threading.Lock()
_cached_voices: list[Voice] | None = None
_cache_expiry: float = 0.0

_ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"


@dataclass(frozen=True)
class Voice:
    key: str
    display_name: str
    provider: str
    voice_id: str
    category: str | None = field(default=None)
    preview_url: str | None = field(default=None)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_available_voices(*, force_refresh: bool = False) -> list[Voice]:
    """
    Return voices available for the configured API key.

    First call: discovers from ElevenLabs (may take ~1s).
    Subsequent calls within 20 min: returns cached list instantly.
    On failure: returns [default_voice] so the system stays functional.
    """
    global _cached_voices, _cache_expiry

    with _cache_lock:
        now = time.monotonic()
        if not force_refresh and _cached_voices is not None and now < _cache_expiry:
            return _cached_voices

        voices = _discover()
        _cached_voices = voices
        _cache_expiry = now + _CACHE_TTL_SECONDS
        return voices


def get_voice(key: str) -> Voice | None:
    """Lookup by key in the current (cached) voice list."""
    return next((v for v in get_available_voices() if v.key == key), None)


def get_voice_by_id(voice_id: str) -> Voice | None:
    """Reverse lookup by voice_id in the current (cached) voice list."""
    return next((v for v in get_available_voices() if v.voice_id == voice_id), None)


# ── Discovery ──────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _discover() -> list[Voice]:
    # Lazy import to avoid circular dependency at module load time.
    from core.platform.config.settings import settings

    api_key = settings.elevenlabs_api_key
    if not api_key:
        logger.warning("voice_discovery: ELEVENLABS_API_KEY not set — cannot discover voices")
        return _fallback(settings)

    req = urllib.request.Request(
        _ELEVENLABS_VOICES_URL,
        headers={"xi-api-key": api_key},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_DISCOVERY_TIMEOUT) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("voice_discovery: ElevenLabs HTTP %s %s — falling back to default", e.code, e.reason)
        return _fallback(settings)
    except Exception as e:
        logger.warning("voice_discovery: ElevenLabs error (%s) — falling back to default", e)
        return _fallback(settings)

    voices: list[Voice] = []
    seen_keys: set[str] = set()

    for v in raw.get("voices", []):
        vid = (v.get("voice_id") or "").strip()
        name = (v.get("name") or vid).strip()
        if not vid:
            continue

        base_key = _slugify(name) or vid[:16]
        key = base_key
        n = 1
        while key in seen_keys:
            key = f"{base_key}_{n}"
            n += 1
        seen_keys.add(key)

        voices.append(Voice(
            key=key,
            display_name=name,
            provider="elevenlabs",
            voice_id=vid,
            category=v.get("category"),
            preview_url=v.get("preview_url"),
        ))

    logger.info("voice_discovery: %d voices discovered from ElevenLabs", len(voices))
    return voices or _fallback(settings)


def _fallback(settings) -> list[Voice]:
    """Single-entry list for the configured default voice when discovery fails."""
    default_id = settings.tts_voice
    if not default_id:
        logger.warning("voice_discovery: no default voice configured — voice list is empty")
        return []
    logger.info("voice_discovery: using fallback voice %s", default_id)
    return [Voice(
        key="default",
        display_name="Default",
        provider="elevenlabs",
        voice_id=default_id,
    )]
