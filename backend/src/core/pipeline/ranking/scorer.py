from __future__ import annotations

import math
from datetime import datetime, timezone

from .config import (
    CATEGORY_TIEBREAK_STRENGTH,
    CLUSTER_WEIGHT_CAP,
    CLUSTER_WEIGHT_MULTIPLIER,
    COVERAGE_K,
    DEFAULT_ENTITY_WEIGHT,
    FRESHNESS_LAMBDA,
    FRESHNESS_MIN,
    IMPORTANCE_HALF,
    PROMINENCE_K,
    RECENCY_DECAY_LAMBDA,
    SOURCE_AUTHORITY_STRENGTH,
    SOURCE_WEIGHT_CAP,
    SOURCE_WEIGHT_MULTIPLIER,
    TIER_AUTHORITY_BONUS,
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


# ── Day-level importance model (coverage-driven) ─────────────────────────────

def _freshness(last_seen_at: datetime, *, now: datetime | None = None) -> float:
    """Bounded recency factor in [FRESHNESS_MIN, 1.0] — a factor, not the driver."""
    reference_now = now or datetime.now(timezone.utc)
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
    hours_ago = max((reference_now - last_seen_at).total_seconds() / 3600.0, 0.0)
    return FRESHNESS_MIN + (1.0 - FRESHNESS_MIN) * math.exp(-FRESHNESS_LAMBDA * hours_ago)


def compute_importance(
    candidate: StoryRankingCandidate,
    category_weight: float,
    *,
    now: datetime | None = None,
) -> tuple[float, float]:
    """
    Coverage-driven day-level importance and its 0–10 normalisation.

    importance = coverage · prominence · freshness · category_tiebreak · source_authority
      coverage         — 1 + COVERAGE_K·log1p(source_count)   (UNCAPPED, dominant)
      prominence       — 1 + PROMINENCE_K·log1p(article_count) + entity heft
      freshness        — bounded [FRESHNESS_MIN, 1]
      category_tiebreak— LIGHT: category_weight rescaled to ≈ ±14% (never a thumb)
      source_authority — LIGHT: lift by the best outlet's credibility tier (breaks
                         ties in the single-source tail; never overrides coverage)

    Returns (importance, normalized_score_0_to_10).
    """
    coverage = 1.0 + COVERAGE_K * math.log1p(max(candidate.source_count, 0))
    entity_heft = max(_entity_weight(candidate.primary_entity_weight) - 1.0, 0.0)
    prominence = 1.0 + PROMINENCE_K * math.log1p(max(candidate.article_count, 0)) + entity_heft
    freshness = _freshness(candidate.last_seen_at, now=now)
    # Rescale the audience category weight (0.30–1.70) into a light nudge.
    category_tiebreak = 1.0 + CATEGORY_TIEBREAK_STRENGTH * (category_weight - 1.0)
    # Light lift by the most-credible outlet on the story (tier 1 wire/flagship →
    # full bonus, unknown → none). Sorts the equal-coverage tail sensibly.
    authority_bonus = TIER_AUTHORITY_BONUS.get(candidate.top_source_tier, 0.0)
    source_authority = 1.0 + SOURCE_AUTHORITY_STRENGTH * authority_bonus

    importance = coverage * prominence * freshness * category_tiebreak * source_authority
    normalized = 10.0 * importance / (importance + IMPORTANCE_HALF)
    return importance, normalized


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

    # Day-level importance model — coverage-driven; the axis thresholds/depth use.
    importance, normalized = compute_importance(candidate, category_weight, now=now)

    return ScoredStory(
        candidate=candidate,
        within_category_score=within_category,
        full_score=within_category * category_weight,
        category_weight=category_weight,
        recency_score=recency,
        entity_weight=entity,
        source_weight=source,
        cluster_weight=cluster,
        importance=importance,
        normalized_score=normalized,
    )
