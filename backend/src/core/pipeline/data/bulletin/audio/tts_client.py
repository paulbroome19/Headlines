"""
ElevenLabs TTS client. Uses stdlib urllib — no new dependencies.

(An OpenAI TTS provider path existed here historically but was never used in prod —
TTS_PROVIDER has only ever been elevenlabs — so it was removed. Re-add from git
history if a second provider is ever needed.)

Configuration (environment variables):
  TTS_PROVIDER            elevenlabs            (default: elevenlabs)
  TTS_VOICE               voice id / name       (default: 19STyYD15bswVz51nqLf — ElevenLabs voice)
  TTS_MODEL               model id              (default: eleven_multilingual_v2)
  TTS_AUDIO_FORMAT        format string         (default: mp3_44100_128)
  TTS_STABILITY           ElevenLabs 0.0–1.0    (default: 0.5)
  TTS_SIMILARITY_BOOST    ElevenLabs 0.0–1.0    (default: 0.75)
  TTS_STYLE               ElevenLabs 0.0–1.0    (default: 0.15)
  TTS_USE_SPEAKER_BOOST   true | false          (default: true)
  ELEVENLABS_API_KEY      required
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

# Defaults
_DEFAULT_PROVIDER = "elevenlabs"
_DEFAULT_VOICE_ELEVENLABS = "19STyYD15bswVz51nqLf"   # keep in sync with settings.tts_voice
_DEFAULT_MODEL_ELEVENLABS = "eleven_multilingual_v2"   # keep in sync with settings.tts_model
_DEFAULT_FORMAT_ELEVENLABS = "mp3_44100_128"


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
    """16-char SHA-256 hex of the segment or bulletin script. Cache key."""
    return hashlib.sha256(script.encode()).hexdigest()[:16]


def _mp3_frame_info(data: bytes) -> tuple[int, int] | None:
    """
    Return (bitrate_kbps, sample_rate_hz) from the first valid MPEG1 Layer3
    frame in data, or None if no frame is found.
    Used by concatenate_mp3 to detect cross-chunk format mismatches.
    """
    _sample_rates = {0: 44100, 1: 48000, 2: 32000}
    _bitrates = {
        1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96,
        8: 112, 9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320,
    }
    offset = 0
    if len(data) >= 10 and data[:3] == b"ID3":
        tag_size = (
            ((data[6] & 0x7F) << 21) |
            ((data[7] & 0x7F) << 14) |
            ((data[8] & 0x7F) << 7) |
             (data[9] & 0x7F)
        )
        offset = 10 + tag_size
    n = len(data)
    while offset < n - 4:
        b = data[offset:offset + 4]
        if b[0] == 0xFF and (b[1] & 0xE0) == 0xE0:
            mpeg_version = (b[1] >> 3) & 0x3
            layer        = (b[1] >> 1) & 0x3
            bitrate_idx  = (b[2] >> 4) & 0xF
            sr_idx       = (b[2] >> 2) & 0x3
            if mpeg_version == 3 and layer == 1 and bitrate_idx in _bitrates:
                sr = _sample_rates.get(sr_idx)
                if sr:
                    return _bitrates[bitrate_idx], sr
        offset += 1
    return None


def _strip_id3(data: bytes) -> bytes:
    """
    Return raw MPEG frames with ID3v2 header and ID3v1 footer removed.
    Used to prepare chunks 2..N for MP3 concatenation.
    """
    start = 0
    if len(data) >= 10 and data[:3] == b"ID3":
        # ID3v2 tag size is a syncsafe integer: 4 × 7-bit bytes
        tag_size = (
            ((data[6] & 0x7F) << 21) |
            ((data[7] & 0x7F) << 14) |
            ((data[8] & 0x7F) << 7) |
             (data[9] & 0x7F)
        )
        start = 10 + tag_size
    end = len(data) - 128 if (len(data) >= 128 and data[-128:-125] == b"TAG") else len(data)
    return data[start:end]


def concatenate_mp3(chunks: list[bytes]) -> bytes:
    """
    Join MP3 byte chunks into a single decodable stream.

    The first chunk is kept verbatim — its ID3v2 header and the initial sync
    word are preserved so metadata-aware players get sample-rate / bitrate
    hints. Every subsequent chunk has its ID3v2 header and ID3v1 footer
    stripped so only raw MPEG frames are appended.

    MP3 frames are self-contained: decoders re-sync at frame boundaries, so
    the join points are transparent to AVAudioPlayer, ExoPlayer, and ffmpeg.
    No re-encoding. No dependencies beyond stdlib.

    Logs first chunk's bitrate/sample_rate (debug). Warns if any later chunk
    differs — indicates a model/voice format change mid-bulletin that will
    produce an audible artifact at that segment boundary.
    """
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]

    first_info = _mp3_frame_info(chunks[0])
    if first_info:
        logger.debug(
            "concatenate_mp3: chunk[0]  bitrate=%dkbps  sample_rate=%dHz",
            *first_info,
        )
    for n, chunk in enumerate(chunks[1:], start=1):
        info = _mp3_frame_info(chunk)
        if info and first_info and info != first_info:
            logger.warning(
                "concatenate_mp3: chunk[%d] format mismatch  "
                "expected=%skbps/%sHz  got=%skbps/%sHz — audible artifact possible",
                n, first_info[0], first_info[1], info[0], info[1],
            )

    return chunks[0] + b"".join(_strip_id3(c) for c in chunks[1:])


def mp3_duration_from_bytes(data: bytes) -> float | None:
    """
    Return MP3 duration in seconds by scanning MPEG1 Layer3 frame headers.
    Called on raw audio bytes immediately after synthesis — no filesystem access.
    Returns None if no valid frames are found.
    """
    _sample_rates = {0: 44100, 1: 48000, 2: 32000}
    _bitrates = {
        1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96,
        8: 112, 9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320,
    }

    offset = 0
    if len(data) >= 10 and data[:3] == b"ID3":
        tag_size = (
            ((data[6] & 0x7F) << 21) |
            ((data[7] & 0x7F) << 14) |
            ((data[8] & 0x7F) << 7)  |
             (data[9] & 0x7F)
        )
        offset = 10 + tag_size

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


def mp3_duration(path: str) -> float | None:
    """
    Return MP3 duration in seconds for a file on disk.
    Returns None if the file cannot be read or parsed.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    return mp3_duration_from_bytes(data)


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
    # voice_settings sent explicitly (ElevenLabs-specific, so read from settings here
    # alongside the api_key rather than threaded through synthesize()). Omitting them
    # lets ElevenLabs fall back to the voice's stored defaults, which differ from the
    # tuned playground sliders and sound more robotic. Env-overridable for tuning by ear.
    body = json.dumps({
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": settings.tts_stability,
            "similarity_boost": settings.tts_similarity_boost,
            "style": settings.tts_style,
            "use_speaker_boost": settings.tts_use_speaker_boost,
        },
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
