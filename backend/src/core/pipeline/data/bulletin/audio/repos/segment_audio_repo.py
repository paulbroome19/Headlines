from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class SegmentAudioRepo:
    """
    Content-addressed cache for all TTS segment types.

    Cache key: (script_hash, voice, model, audio_format)
    script_hash = sha256(segment_text)[:16] — identical text always maps to the
    same audio bytes regardless of which user or bulletin requested it.

    segment_type ('story' | 'intro' | 'outro' | 'transition' | 'sponsorship' |
    'alert') is recorded on insert for analytics only. It is NOT part of cache
    lookup — the same text hash always maps to exactly one audio file.

    Two intentionally distinct write paths:
      insert()       — ON CONFLICT DO NOTHING  (normal miss; first writer wins)
      upsert_stale() — ON CONFLICT DO UPDATE   (backing file was missing;
                        refreshes storage_path/audio_url, preserves analytics)

    Known limitation: name-specific intro/outro segments (e.g. "Good morning,
    Siobhan") are cached globally. If ElevenLabs mispronounces a name on first
    generation, that mispronunciation is served to every user with that name
    until the row is manually deleted and regenerated.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_cached(
        self,
        *,
        script_hash: str,
        voice: str,
        model: str,
        audio_format: str,
    ) -> dict[str, Any] | None:
        """
        Return cached audio metadata for this (hash, voice, model, format),
        or None if no entry exists.

        Never receives segment_type — the cache is blind to segment classification.
        """
        row = self.db.execute(
            text("""
                SELECT id, script_hash, voice, model, audio_format,
                       segment_type, character_count, storage_path, audio_url,
                       duration_seconds, created_at
                FROM data.segment_audio
                WHERE script_hash  = :script_hash
                  AND voice        = :voice
                  AND model        = :model
                  AND audio_format = :audio_format
                LIMIT 1
            """),
            {
                "script_hash": script_hash,
                "voice": voice,
                "model": model,
                "audio_format": audio_format,
            },
        ).mappings().first()
        return dict(row) if row else None

    def insert(
        self,
        *,
        script_hash: str,
        voice: str,
        model: str,
        audio_format: str,
        segment_type: str,
        storage_path: str,
        audio_url: str | None = None,
        character_count: int | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        """
        Insert a new audio row. ON CONFLICT DO NOTHING — first writer wins.
        Concurrent inserts for the same hash are safe: both synthesize the same
        bytes, one row lands, both callers get the id.

        Returns the id of the inserted or pre-existing row.
        """
        row_id = self.db.execute(
            text("""
                INSERT INTO data.segment_audio
                    (script_hash, voice, model, audio_format, segment_type,
                     character_count, storage_path, audio_url, duration_seconds)
                VALUES
                    (:script_hash, :voice, :model, :audio_format, :segment_type,
                     :character_count, :storage_path, :audio_url, :duration_seconds)
                ON CONFLICT (script_hash, voice, model, audio_format) DO NOTHING
                RETURNING id
            """),
            {
                "script_hash": script_hash,
                "voice": voice,
                "model": model,
                "audio_format": audio_format,
                "segment_type": segment_type,
                "character_count": character_count,
                "storage_path": storage_path,
                "audio_url": audio_url,
                "duration_seconds": duration_seconds,
            },
        ).scalar()
        if row_id is None:
            row_id = self.db.execute(
                text("""
                    SELECT id FROM data.segment_audio
                    WHERE script_hash  = :script_hash
                      AND voice        = :voice
                      AND model        = :model
                      AND audio_format = :audio_format
                """),
                {
                    "script_hash": script_hash,
                    "voice": voice,
                    "model": model,
                    "audio_format": audio_format,
                },
            ).scalar()
        return int(row_id)

    def get_cached_batch(
        self,
        *,
        script_hashes: list[str],
        voice: str,
        model: str,
        audio_format: str,
    ) -> dict[str, Any]:
        """
        Return {script_hash: row} for every hash in script_hashes that has a
        cached audio row. Hashes with no row are absent from the result.

        Reduces N single-row lookups to 1 query — used by synthesize_segments().
        """
        if not script_hashes:
            return {}
        rows = self.db.execute(
            text("""
                SELECT id, script_hash, voice, model, audio_format,
                       segment_type, character_count, storage_path, audio_url,
                       duration_seconds, created_at
                FROM data.segment_audio
                WHERE script_hash  = ANY(:hashes)
                  AND voice        = :voice
                  AND model        = :model
                  AND audio_format = :audio_format
            """),
            {
                "hashes": script_hashes,
                "voice": voice,
                "model": model,
                "audio_format": audio_format,
            },
        ).mappings().all()
        return {r["script_hash"]: dict(r) for r in rows}

    def upsert_stale(
        self,
        *,
        script_hash: str,
        voice: str,
        model: str,
        audio_format: str,
        segment_type: str,
        character_count: int | None = None,
        storage_path: str,
        audio_url: str | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        """
        Refresh the storage location of a stale row (backing file was missing).

        ON CONFLICT DO UPDATE:
          Updates:   storage_path, audio_url, duration_seconds, updated_at = now()
          Preserves: segment_type, character_count, created_at

        The INSERT branch handles the rare race where the row was deleted
        between the cache check and this call. In normal operation it never
        fires — we only call upsert_stale() after a confirmed cache hit.

        Returns the id of the updated (or freshly inserted) row.
        """
        row_id = self.db.execute(
            text("""
                INSERT INTO data.segment_audio
                    (script_hash, voice, model, audio_format, segment_type,
                     character_count, storage_path, audio_url, duration_seconds,
                     updated_at)
                VALUES
                    (:script_hash, :voice, :model, :audio_format, :segment_type,
                     :character_count, :storage_path, :audio_url, :duration_seconds,
                     now())
                ON CONFLICT (script_hash, voice, model, audio_format) DO UPDATE SET
                    storage_path     = EXCLUDED.storage_path,
                    audio_url        = EXCLUDED.audio_url,
                    duration_seconds = EXCLUDED.duration_seconds,
                    updated_at       = now()
                RETURNING id
            """),
            {
                "script_hash": script_hash,
                "voice": voice,
                "model": model,
                "audio_format": audio_format,
                "segment_type": segment_type,
                "character_count": character_count,
                "storage_path": storage_path,
                "audio_url": audio_url,
                "duration_seconds": duration_seconds,
            },
        ).scalar()
        return int(row_id)
