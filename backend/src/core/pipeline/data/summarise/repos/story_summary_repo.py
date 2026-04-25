from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class StorySummaryRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_existing_story_ids(self, ranking_run_id: int) -> set[str]:
        """Return story_ids that already have a summary for this ranking run."""
        rows = self.db.execute(
            text("""
                SELECT story_id
                FROM data.story_summaries
                WHERE ranking_run_id = :run_id
            """),
            {"run_id": ranking_run_id},
        ).fetchall()
        return {r[0] for r in rows}

    def get_cached(
        self,
        story_id: str,
        content_hash: str,
        model: str,
    ) -> dict[str, Any] | None:
        """
        Look up a summary from a previous run with the same story content and model.
        Returns the most recent matching row, or None.
        """
        row = self.db.execute(
            text("""
                SELECT headline, summary_text, why_it_matters, audio_script,
                       model, summary_version, confidence
                FROM data.story_summaries
                WHERE story_id    = :story_id
                  AND content_hash = :content_hash
                  AND model        = :model
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"story_id": story_id, "content_hash": content_hash, "model": model},
        ).mappings().first()
        return dict(row) if row else None

    def get_by_ranking_run(
        self,
        ranking_run_id: int,
        *,
        ordered_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return all summaries for a ranking run.
        If ordered_ids is provided, sort results to match that ranking order.
        """
        rows = self.db.execute(
            text("""
                SELECT id, story_id, ranking_run_id, content_hash,
                       headline, summary_text, why_it_matters, audio_script,
                       model, summary_version, confidence, created_at
                FROM data.story_summaries
                WHERE ranking_run_id = :run_id
            """),
            {"run_id": ranking_run_id},
        ).mappings().all()

        result = [dict(r) for r in rows]

        if ordered_ids:
            order_map = {sid: i for i, sid in enumerate(ordered_ids)}
            result.sort(key=lambda r: order_map.get(str(r["story_id"]), len(ordered_ids)))

        return result
