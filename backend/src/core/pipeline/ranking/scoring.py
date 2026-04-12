from __future__ import annotations

import math
from datetime import datetime, timezone

from .config import (
    CATEGORY_WEIGHTS,
    CLUSTER_WEIGHT_CAP,
    CLUSTER_WEIGHT_MULTIPLIER,
    DEFAULT_CATEGORY_WEIGHT,
    DEFAULT_ENTITY_WEIGHT,
    RECENCY_DECAY_LAMBDA,
    SOURCE_WEIGHT_CAP,
    SOURCE_WEIGHT_MULTIPLIER,
)
from .models import StoryRankingCandidate


def get_category_weight(category_slug: str | None) -> float:
    """
    Resolve a category weight using:
    1. exact match
    2. prefix fallback (e.g. sport.football.premier-league -> sport)
    3. default weight
    """
    if not category_slug:
        return DEFAULT_CATEGORY_WEIGHT

    if category_slug in CATEGORY_WEIGHTS:
        return CATEGORY_WEIGHTS[category_slug]

    parts = category_slug.split(".")
    while len(parts) > 1:
        parts.pop()
        fallback = ".".join(parts)
        if fallback in CATEGORY_WEIGHTS:
            return CATEGORY_WEIGHTS[fallback]

    return DEFAULT_CATEGORY_WEIGHT


def get_entity_weight(primary_entity_weight: float | None) -> float:
    """
    Use the configured primary entity weight when present,
    otherwise fall back to the default neutral weight.
    """
    if primary_entity_weight is None:
        return DEFAULT_ENTITY_WEIGHT
    return max(primary_entity_weight, 0.0)


def get_recency_score(
    last_seen_at: datetime,
    *,
    now: datetime | None = None,
) -> float:
    """
    Exponential decay based on hours since the story was last seen.
    """
    reference_now = now or datetime.now(timezone.utc)

    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)

    hours_since_last_seen = max(
        (reference_now - last_seen_at).total_seconds() / 3600.0,
        0.0,
    )

    return math.exp(-RECENCY_DECAY_LAMBDA * hours_since_last_seen)


def get_source_weight(source_count: int) -> float:
    """
    Small capped boost for source diversity.
    """
    safe_count = max(source_count, 0)
    boost = min(math.log1p(safe_count) * SOURCE_WEIGHT_MULTIPLIER, SOURCE_WEIGHT_CAP)
    return 1.0 + boost


def get_cluster_weight(article_count: int) -> float:
    """
    Small capped boost for stronger clusters.
    """
    safe_count = max(article_count, 0)
    boost = min(
        math.log1p(safe_count) * CLUSTER_WEIGHT_MULTIPLIER,
        CLUSTER_WEIGHT_CAP,
    )
    return 1.0 + boost


def get_base_score(
    candidate: StoryRankingCandidate,
    *,
    now: datetime | None = None,
) -> float:
    """
    Compute the raw base score before any selection penalties.
    """
    recency_score = get_recency_score(candidate.last_seen_at, now=now)
    category_weight = get_category_weight(candidate.primary_category)
    entity_weight = get_entity_weight(candidate.primary_entity_weight)
    source_weight = get_source_weight(candidate.source_count)
    cluster_weight = get_cluster_weight(candidate.article_count)

    return (
        recency_score
        * category_weight
        * entity_weight
        * source_weight
        * cluster_weight
    )