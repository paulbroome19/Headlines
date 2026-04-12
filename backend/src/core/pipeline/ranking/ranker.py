from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .models import RankedStory, StoryRankingCandidate
from .scoring import (
    get_base_score,
    get_category_weight,
    get_cluster_weight,
    get_entity_weight,
    get_recency_score,
    get_source_weight,
)
from .selection import apply_selection


class StoryRanker:
    """
    Deterministic v1 story ranker.

    Responsibilities:
    - score candidates
    - sort by base score descending
    - apply hybrid diversity-aware selection
    """

    def rank(
        self,
        candidates: Iterable[StoryRankingCandidate],
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[RankedStory]:
        ranked = [self._rank_candidate(candidate, now=now) for candidate in candidates]
        ranked.sort(key=lambda story: story.base_score, reverse=True)
        return apply_selection(ranked, limit=limit)

    def _rank_candidate(
        self,
        candidate: StoryRankingCandidate,
        *,
        now: datetime | None = None,
    ) -> RankedStory:
        recency_score = get_recency_score(candidate.last_seen_at, now=now)
        category_weight = get_category_weight(candidate.primary_category)
        entity_weight = get_entity_weight(candidate.primary_entity_weight)
        source_weight = get_source_weight(candidate.source_count)
        cluster_weight = get_cluster_weight(candidate.article_count)

        base_score = get_base_score(candidate, now=now)

        return RankedStory(
            story_id=candidate.story_id,
            title=candidate.title,
            base_score=base_score,
            adjusted_score=base_score,
            last_seen_at=candidate.last_seen_at,
            article_count=candidate.article_count,
            source_count=candidate.source_count,
            primary_category=candidate.primary_category,
            primary_entity_id=candidate.primary_entity_id,
            primary_entity_name=candidate.primary_entity_name,
            primary_topic_key=candidate.primary_topic_key,
            recency_score=recency_score,
            category_weight=category_weight,
            entity_weight=entity_weight,
            source_weight=source_weight,
            cluster_weight=cluster_weight,
        )