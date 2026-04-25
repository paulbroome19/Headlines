from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class BulletinRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, bulletin_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            text("""
                SELECT id, ranking_run_id, request_hash, filters,
                       story_count, script, segments, created_at
                FROM data.bulletins
                WHERE id = :bulletin_id
            """),
            {"bulletin_id": bulletin_id},
        ).mappings().first()
        return dict(row) if row else None

    def get_by_run_and_hash(
        self,
        ranking_run_id: int,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            text("""
                SELECT id, ranking_run_id, request_hash, filters,
                       story_count, script, segments, created_at
                FROM data.bulletins
                WHERE ranking_run_id = :run_id
                  AND request_hash   = :request_hash
            """),
            {"run_id": ranking_run_id, "request_hash": request_hash},
        ).mappings().first()
        return dict(row) if row else None

    def get_latest(self) -> dict[str, Any] | None:
        row = self.db.execute(
            text("""
                SELECT id, ranking_run_id, request_hash, filters,
                       story_count, script, segments, created_at
                FROM data.bulletins
                ORDER BY created_at DESC
                LIMIT 1
            """)
        ).mappings().first()
        return dict(row) if row else None

    def insert(
        self,
        *,
        ranking_run_id: int,
        request_hash: str,
        filters: dict,
        story_count: int,
        script: str,
        segments: list[dict],
    ) -> int:
        run_id = self.db.execute(
            text("""
                INSERT INTO data.bulletins
                    (ranking_run_id, request_hash, filters, story_count, script, segments)
                VALUES
                    (:ranking_run_id, :request_hash, :filters, :story_count, :script, :segments)
                RETURNING id
            """),
            {
                "ranking_run_id": ranking_run_id,
                "request_hash": request_hash,
                "filters": json.dumps(filters),
                "story_count": story_count,
                "script": script,
                "segments": json.dumps(segments),
            },
        ).scalar_one()
        return int(run_id)
