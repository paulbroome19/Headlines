from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


class AudioOutputRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def exists_for_scripts_output(self, scripts_output_id: int) -> bool:
        row = self.db.execute(
            text("SELECT 1 FROM audio.outputs WHERE scripts_output_id = :sid LIMIT 1"),
            {"sid": int(scripts_output_id)},
        ).first()
        return row is not None

    def create(
        self,
        *,
        feed_id: int,
        scripts_output_id: int,
        variant: str,
        status: str,
        format: str,
        storage_path: str,
        duration_seconds: int | None,
    ) -> int:
        out_id = self.db.execute(
            text(
                """
                INSERT INTO audio.outputs
                  (feed_id, scripts_output_id, variant, status, format, storage_path, duration_seconds)
                VALUES
                  (:feed_id, :scripts_output_id, :variant, :status, :format, :storage_path, :duration_seconds)
                RETURNING id
                """
            ),
            {
                "feed_id": int(feed_id),
                "scripts_output_id": int(scripts_output_id),
                "variant": variant,
                "status": status,
                "format": format,
                "storage_path": storage_path,
                "duration_seconds": duration_seconds,
            },
        ).scalar_one()
        return int(out_id)