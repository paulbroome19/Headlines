from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.pipeline.ranking.models import RankedStory


def _story_to_dict(story: RankedStory) -> dict[str, Any]:
    return {
        "story_id": story.story_id,
        "title": story.title,
        "full_score": story.full_score,
        "adjusted_score": story.adjusted_score,
        "within_category_score": story.within_category_score,
        "last_seen_at": story.last_seen_at.isoformat(),
        "article_count": story.article_count,
        "source_count": story.source_count,
        "primary_category": story.primary_category,
        "primary_entity_id": story.primary_entity_id,
        "primary_entity_name": story.primary_entity_name,
        "primary_topic_key": story.primary_topic_key,
        "recency_score": story.recency_score,
        "category_weight": story.category_weight,
        "entity_weight": story.entity_weight,
        "source_weight": story.source_weight,
        "cluster_weight": story.cluster_weight,
    }


class RankingRunRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def insert_run(
        self,
        *,
        batch_id: str,
        candidate_count: int,
        top_stories: list[RankedStory],
        briefing: list[RankedStory],
    ) -> int:
        import json

        top_stories_json = json.dumps([_story_to_dict(s) for s in top_stories])
        briefing_json = json.dumps([_story_to_dict(s) for s in briefing])

        run_id = self.db.execute(
            text(
                """
                INSERT INTO data.ranking_runs (
                    batch_id,
                    candidate_count,
                    top_stories,
                    briefing
                )
                VALUES (
                    :batch_id,
                    :candidate_count,
                    :top_stories,
                    :briefing
                )
                RETURNING id
                """
            ),
            {
                "batch_id": batch_id,
                "candidate_count": candidate_count,
                "top_stories": top_stories_json,
                "briefing": briefing_json,
            },
        ).scalar_one()

        return int(run_id)

    def get_latest(self) -> dict[str, Any] | None:
        row = self.db.execute(
            text(
                """
                SELECT id, batch_id, candidate_count, top_stories, briefing, created_at
                FROM data.ranking_runs
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        ).mappings().first()

        return dict(row) if row else None
