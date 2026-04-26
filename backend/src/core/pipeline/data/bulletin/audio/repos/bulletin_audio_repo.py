from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class BulletinAudioRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_cached(
        self,
        *,
        bulletin_id: int,
        script_hash: str,
        provider: str,
        voice: str,
        model: str,
        audio_format: str,
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            text("""
                SELECT id, bulletin_id, script_hash, provider, voice, model,
                       audio_format, storage_path, audio_url, duration_seconds, created_at
                FROM data.bulletin_audio
                WHERE bulletin_id  = :bulletin_id
                  AND script_hash  = :script_hash
                  AND provider     = :provider
                  AND voice        = :voice
                  AND model        = :model
                  AND audio_format = :audio_format
                LIMIT 1
            """),
            {
                "bulletin_id": bulletin_id,
                "script_hash": script_hash,
                "provider": provider,
                "voice": voice,
                "model": model,
                "audio_format": audio_format,
            },
        ).mappings().first()
        return dict(row) if row else None

    def insert(
        self,
        *,
        bulletin_id: int,
        script_hash: str,
        provider: str,
        voice: str,
        model: str,
        audio_format: str,
        storage_path: str,
        audio_url: str | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        row_id = self.db.execute(
            text("""
                INSERT INTO data.bulletin_audio
                    (bulletin_id, script_hash, provider, voice, model, audio_format,
                     storage_path, audio_url, duration_seconds)
                VALUES
                    (:bulletin_id, :script_hash, :provider, :voice, :model, :audio_format,
                     :storage_path, :audio_url, :duration_seconds)
                ON CONFLICT (bulletin_id, script_hash, provider, voice, model, audio_format)
                DO NOTHING
                RETURNING id
            """),
            {
                "bulletin_id": bulletin_id,
                "script_hash": script_hash,
                "provider": provider,
                "voice": voice,
                "model": model,
                "audio_format": audio_format,
                "storage_path": storage_path,
                "audio_url": audio_url,
                "duration_seconds": duration_seconds,
            },
        ).scalar()
        # ON CONFLICT DO NOTHING returns NULL if the row already existed.
        # Fetch the existing id in that case.
        if row_id is None:
            row_id = self.db.execute(
                text("""
                    SELECT id FROM data.bulletin_audio
                    WHERE bulletin_id  = :bulletin_id
                      AND script_hash  = :script_hash
                      AND provider     = :provider
                      AND voice        = :voice
                      AND model        = :model
                      AND audio_format = :audio_format
                """),
                {
                    "bulletin_id": bulletin_id,
                    "script_hash": script_hash,
                    "provider": provider,
                    "voice": voice,
                    "model": model,
                    "audio_format": audio_format,
                },
            ).scalar()
        return int(row_id)
