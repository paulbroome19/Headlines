from __future__ import annotations

import math
from datetime import datetime, timezone

from .config import (
    CLUSTER_WEIGHT_CAP,
    CLUSTER_WEIGHT_MULTIPLIER,
    DEFAULT_ENTITY_WEIGHT,
    RECENCY_DECAY_LAMBDA,
    SOURCE_WEIGHT_CAP,
    SOURCE_WEIGHT_MULTIPLIER,
)
from .models import ScoredStory, StoryRankingCandidate


def _recency_score(last_seen_at: datetime, *, now: datetime | None = None) -> float:
    reference_now = now or datetime.now(timezone.utc)
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
    hours_ago = max((reference_now - last_seen_at).total_seconds() / 3600.0, 0.0)
    return math.exp(-RECENCY_DECAY_LAMBDA * hours_ago)


def _entity_weight(primary_entity_weight: float | None) -> float:
    if primary_entity_weight is None:
        return DEFAULT_ENTITY_WEIGHT
    return max(primary_entity_weight, 0.0)


def _source_weight(source_count: int) -> float:
    boost = min(math.log1p(max(source_count, 0)) * SOURCE_WEIGHT_MULTIPLIER, SOURCE_WEIGHT_CAP)
    return 1.0 + boost


def _cluster_weight(article_count: int) -> float:
    boost = min(math.log1p(max(article_count, 0)) * CLUSTER_WEIGHT_MULTIPLIER, CLUSTER_WEIGHT_CAP)
    return 1.0 + boost


def score_story(
    candidate: StoryRankingCandidate,
    category_weight: float,
    *,
    now: datetime | None = None,
) -> ScoredStory:
    """
    Score an individual story.

    within_category_score reflects story quality (recency, entity importance,
    source breadth, cluster strength) independently of category priority.

    full_score applies the category weight on top, making stories comparable
    across categories.
    """
    recency = _recency_score(candidate.last_seen_at, now=now)
    entity = _entity_weight(candidate.primary_entity_weight)
    source = _source_weight(candidate.source_count)
    cluster = _cluster_weight(candidate.article_count)
    within_category = recency * entity * source * cluster

    return ScoredStory(
        candidate=candidate,
        within_category_score=within_category,
        full_score=within_category * category_weight,
        category_weight=category_weight,
        recency_score=recency,
        entity_weight=entity,
        source_weight=source,
        cluster_weight=cluster,
    )
