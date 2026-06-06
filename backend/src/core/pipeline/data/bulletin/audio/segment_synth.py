"""
Segment-level TTS synthesis with global content-addressed cache.

Public API
----------
synthesize_segment()   — single segment; used by the Step 4 hot-generation worker
synthesize_segments()  — multi-segment with parallel cache-hit fetching;
                         used by _generate_audio() in data.py

Cache semantics
---------------
Key: (script_hash, voice, model, audio_format)
script_hash = sha256(segment_text)[:16]

Identical text → same hash → same cached audio, regardless of which user,
bulletin, or segment type produced the request. A transition phrase generated
for User A is served instantly to User B. An intro for "Alex" generated on
Monday is served on Friday.

segment_type ('story' | 'intro' | 'outro' | 'transition' | 'sponsorship' |
'alert') is analytics metadata — never part of cache lookup.

Two write paths (intentionally distinct):
  Normal miss  → insert()       ON CONFLICT DO NOTHING (first writer wins)
  Stale row    → upsert_stale() ON CONFLICT DO UPDATE  (refreshes storage only)

Known limitations
-----------------
- Name-specific intros ("Good morning, Siobhan") cached globally by text hash.
  ElevenLabs mispronunciations persist until the segment_audio row is manually
  deleted and regenerated.
- S3-backed cache hits require an HTTP download before concatenation (Step 3's
  client-side sequential playback resolves this entirely).
"""
from __future__ import annotations

import logging
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from core.pipeline.data.bulletin.audio.tts_client import (
    compute_script_hash,
    ext_from_format,
    mp3_duration_from_bytes,
    synthesize,
    tts_provider,
)
from core.pipeline.data.bulletin.audio.repos.segment_audio_repo import SegmentAudioRepo
from core.platform.db.session import SessionLocal
from core.platform.storage.audio_storage import get_audio_storage_provider

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SECONDS = 30
_MAX_PARALLEL_FETCHES = 8


# ── Public API ─────────────────────────────────────────────────────────────────

def synthesize_segment(
    segment_text: str,
    *,
    segment_type: str,
    voice: str,
    model: str,
    audio_format: str,
) -> bytes | None:
    """
    Return audio bytes for a single segment (hot-generation worker entry point).
    Delegates to synthesize_segments() so cache/TTS/store logic is not duplicated.
    Returns None on TTS failure (fail-closed).
    """
    results = synthesize_segments(
        [{"text": segment_text, "type": segment_type}],
        voice=voice,
        model=model,
        audio_format=audio_format,
    )
    return results[0]


def synthesize_segments(
    segments: list[dict],
    *,
    voice: str,
    model: str,
    audio_format: str,
) -> list[bytes | None]:
    """
    Synthesize audio for all bulletin segments, parallelising storage fetches
    for cache hits.

    segments: list of dicts with at least {"text": str, "type": str}.
    Empty-text segments (pause transitions) map to None without any work.

    Returns bytes in the same order as input. None means either an empty
    segment or a TTS failure.

    Flow:
      1. Filter non-empty, compute hashes
      2. Single batch DB query — 1 round-trip for all N hashes
      3. Parallel _fetch_bytes() for hits — ThreadPoolExecutor up to 8 workers
         (sync equivalent of asyncio.gather; Step 3 supersedes server-side
         concatenation, so this path becomes the fallback for non-streaming
         clients)
      4. Sequential TTS for misses and stale re-generates
      5. Log wall-clock time + hit/miss/stale counts (baseline for Step 3)
    """
    t0 = time.monotonic()
    result: list[bytes | None] = [None] * len(segments)

    active = [
        (i, seg) for i, seg in enumerate(segments)
        if (seg.get("text") or "").strip()
    ]
    if not active:
        return result

    active_map = {i: seg for i, seg in active}
    hashes = {i: compute_script_hash(seg["text"]) for i, seg in active}

    # ── 1. Batch cache check ───────────────────────────────────────────────────
    with SessionLocal() as db:
        cached_map = SegmentAudioRepo(db).get_cached_batch(
            script_hashes=list(hashes.values()),
            voice=voice,
            model=model,
            audio_format=audio_format,
        )

    hits   = {i: cached_map[h] for i, h in hashes.items() if h in cached_map}
    misses = [i for i in active_map if i not in hits]

    # ── 2. Parallel fetch for cache hits ──────────────────────────────────────
    stale: list[int] = []

    if hits:
        def _fetch_one(item: tuple[int, dict]) -> tuple[int, bytes | None]:
            idx, row = item
            return idx, _fetch_bytes(row)

        # hits is a dict built from enumerate order — Python 3.7+ guarantees
        # insertion order is preserved. If hits is ever refactored to a set or
        # non-ordered mapping, stale-list population below breaks silently.
        with ThreadPoolExecutor(max_workers=min(len(hits), _MAX_PARALLEL_FETCHES)) as pool:
            for idx, audio in pool.map(_fetch_one, hits.items()):
                if audio is not None:
                    result[idx] = audio
                else:
                    # Cached row exists but backing file is gone — re-generate
                    # and upsert to fix the stale pointer.
                    stale.append(idx)
                    misses.append(idx)

    # ── 3. Sequential TTS for misses + stale re-generates ─────────────────────
    for i in sorted(misses):
        seg = active_map[i]
        result[i] = _synthesize_and_cache(
            seg["text"],
            segment_type=seg.get("type", "story"),
            script_hash=hashes[i],
            stale_row=hits.get(i),   # non-None → upsert_stale(); None → insert()
            voice=voice,
            model=model,
            audio_format=audio_format,
        )

    # ── 4. Timing log ─────────────────────────────────────────────────────────
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    n_clean_hits = len(hits) - len(stale)
    n_new_misses = len(misses) - len(stale)
    logger.info(
        "synthesize_segments: %d segments  hits=%d  misses=%d  stale=%d  elapsed=%dms",
        len(active), n_clean_hits, n_new_misses, len(stale), elapsed_ms,
    )

    return result


# ── Private helpers ────────────────────────────────────────────────────────────

def _fetch_bytes(cached: dict) -> bytes | None:
    """
    Retrieve audio bytes from a segment_audio row.
    Tries local storage_path first (no network), falls back to audio_url (S3/CDN).
    Returns None if neither source is readable — caller treats this as stale.
    """
    storage_path = cached.get("storage_path", "")
    if storage_path and os.path.isfile(storage_path):
        with open(storage_path, "rb") as f:
            return f.read()

    audio_url = cached.get("audio_url")
    if audio_url:
        try:
            with urllib.request.urlopen(audio_url, timeout=_FETCH_TIMEOUT_SECONDS) as resp:
                return resp.read()
        except Exception as exc:
            logger.warning("_fetch_bytes: failed to fetch %s: %s", audio_url, exc)

    return None


def _synthesize_and_cache(
    text: str,
    *,
    segment_type: str,
    script_hash: str,
    stale_row: dict | None,
    voice: str,
    model: str,
    audio_format: str,
) -> bytes | None:
    """
    Call TTS for a cache miss or stale segment, then persist result.

    stale_row is not None  →  upsert_stale(): updates storage_path/audio_url/
                               updated_at; preserves segment_type, character_count,
                               created_at.
    stale_row is None      →  insert(): ON CONFLICT DO NOTHING; first writer wins.
    """
    audio = synthesize(
        text,
        provider=tts_provider(),
        voice=voice,
        model=model,
        audio_format=audio_format,
    )
    if audio is None:
        logger.warning(
            "_synthesize_and_cache FAIL  type=%-12s hash=%s — TTS returned None",
            segment_type, script_hash,
        )
        return None

    duration = mp3_duration_from_bytes(audio)
    ext = ext_from_format(audio_format)
    stored = get_audio_storage_provider().store(audio, f"seg_{script_hash}.{ext}")

    with SessionLocal() as db:
        repo = SegmentAudioRepo(db)
        if stale_row is not None:
            repo.upsert_stale(
                script_hash=script_hash,
                voice=voice,
                model=model,
                audio_format=audio_format,
                segment_type=stale_row["segment_type"],
                character_count=len(text),
                storage_path=stored.storage_path,
                audio_url=stored.audio_url,
                duration_seconds=duration,
            )
            logger.info(
                "_synthesize_and_cache STALE-FIX  type=%-12s hash=%s  duration=%.2fs",
                segment_type, script_hash, duration or 0,
            )
        else:
            repo.insert(
                script_hash=script_hash,
                voice=voice,
                model=model,
                audio_format=audio_format,
                segment_type=segment_type,
                storage_path=stored.storage_path,
                audio_url=stored.audio_url,
                character_count=len(text),
                duration_seconds=duration,
            )
        db.commit()

    return audio
