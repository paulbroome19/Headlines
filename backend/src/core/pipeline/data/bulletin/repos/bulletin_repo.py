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

    def update_segments(self, bulletin_id: int, segments: list[dict]) -> None:
        """Patch the segments jsonb only — used during streaming assembly to fill in
        segment texts (intro/story/transition) as summaries land, so /readiness can hash
        and report them. Leaves script='' until finalised (the in-progress marker)."""
        self.db.execute(
            text("UPDATE data.bulletins SET segments = :segments WHERE id = :id"),
            {"segments": json.dumps(segments), "id": bulletin_id},
        )

    def update_after_assembly(
        self, bulletin_id: int, *, segments: list[dict], script: str, story_count: int,
    ) -> None:
        """Finalise a skeleton row once background assembly completes. Setting a non-empty
        `script` is the 'fully assembled' signal: get_by_run_and_hash's caller treats a row
        with script='' as in-progress (join it) and script<>'' as a cache hit."""
        self.db.execute(
            text("""
                UPDATE data.bulletins
                SET segments = :segments, script = :script, story_count = :story_count
                WHERE id = :id
            """),
            {"segments": json.dumps(segments), "script": script,
             "story_count": story_count, "id": bulletin_id},
        )
