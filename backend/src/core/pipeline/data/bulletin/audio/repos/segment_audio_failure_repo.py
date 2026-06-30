from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


class SegmentAudioFailureRepo:
    """
    Records TTS segments that GENUINELY failed to generate after retries, so the
    API can report an honest, distinguishable "failed" state to iOS (vs "still
    generating, keep polling").

    Key: (script_hash, voice, model, audio_format) — same as the audio cache.
    A row here means "this exact segment text failed N synthesis attempts". It is
    cleared automatically when the segment later synthesises successfully, so a
    transient failure that recovers stops being reported as failed.

    Separate table from segment_audio (the hot cache) by design — the failure
    bookkeeping never touches the working generation/cache path.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        script_hash: str,
        voice: str,
        model: str,
        audio_format: str,
        attempts: int,
        last_error: str,
    ) -> None:
        """Upsert a failure row (counts attempts, keeps the latest error)."""
        self.db.execute(
            text("""
                INSERT INTO data.segment_audio_failures
                    (script_hash, voice, model, audio_format, attempts, last_error, failed_at)
                VALUES
                    (:script_hash, :voice, :model, :audio_format, :attempts, :last_error, now())
                ON CONFLICT (script_hash, voice, model, audio_format) DO UPDATE SET
                    attempts   = data.segment_audio_failures.attempts + EXCLUDED.attempts,
                    last_error = EXCLUDED.last_error,
                    failed_at  = now()
            """),
            {
                "script_hash": script_hash,
                "voice": voice,
                "model": model,
                "audio_format": audio_format,
                "attempts": attempts,
                "last_error": (last_error or "")[:1000],
            },
        )

    def clear(
        self,
        *,
        script_hash: str,
        voice: str,
        model: str,
        audio_format: str,
    ) -> None:
        """Remove any failure row — called when the segment later succeeds."""
        self.db.execute(
            text("""
                DELETE FROM data.segment_audio_failures
                WHERE script_hash = :script_hash
                  AND voice = :voice AND model = :model AND audio_format = :audio_format
            """),
            {"script_hash": script_hash, "voice": voice, "model": model, "audio_format": audio_format},
        )

    def is_failed(
        self,
        *,
        script_hash: str,
        voice: str,
        model: str,
        audio_format: str,
    ) -> dict | None:
        """Return the failure row (attempts, last_error) for one hash, or None."""
        row = self.db.execute(
            text("""
                SELECT attempts, last_error
                FROM data.segment_audio_failures
                WHERE script_hash = :script_hash
                  AND voice = :voice AND model = :model AND audio_format = :audio_format
            """),
            {"script_hash": script_hash, "voice": voice, "model": model, "audio_format": audio_format},
        ).mappings().first()
        return dict(row) if row else None

    def get_failed_batch(
        self,
        *,
        script_hashes: list[str],
        voice: str,
        model: str,
        audio_format: str,
    ) -> set[str]:
        """Return the subset of script_hashes that have a failure row."""
        if not script_hashes:
            return set()
        rows = self.db.execute(
            text("""
                SELECT script_hash
                FROM data.segment_audio_failures
                WHERE script_hash = ANY(:hashes)
                  AND voice = :voice AND model = :model AND audio_format = :audio_format
            """),
            {"hashes": script_hashes, "voice": voice, "model": model, "audio_format": audio_format},
        ).scalars().all()
        return set(rows)
