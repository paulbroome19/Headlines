from __future__ import annotations

from collections import defaultdict

from .category_buckets import get_category_bucket
from .config import (
    BUCKET_MAX_STORIES,
    BUCKET_REPEAT_PENALTY,
    DEFAULT_BUCKET_MAX_STORIES,
    MIN_ADJUSTED_SCORE,
    REPEATED_PRIMARY_ENTITY_PENALTY,
    REPEATED_TOPIC_PENALTY,
)
from .models import RankedStory, ScoredStory


def _to_ranked(scored: ScoredStory, *, adjusted_score: float) -> RankedStory:
    c = scored.candidate
    return RankedStory(
        story_id=c.story_id,
        title=c.title,
        full_score=scored.full_score,
        adjusted_score=adjusted_score,
        within_category_score=scored.within_category_score,
        last_seen_at=c.last_seen_at,
        article_count=c.article_count,
        source_count=c.source_count,
        primary_category=c.primary_category,
        primary_entity_id=c.primary_entity_id,
        primary_entity_name=c.primary_entity_name,
        primary_topic_key=c.primary_topic_key,
        recency_score=scored.recency_score,
        category_weight=scored.category_weight,
        entity_weight=scored.entity_weight,
        source_weight=scored.source_weight,
        cluster_weight=scored.cluster_weight,
    )


def _in_user_categories(primary_category: str | None, user_categories: list[str] | None) -> bool:
    if user_categories is None:
        return True
    if not primary_category:
        return False
    for uc in user_categories:
        if primary_category == uc or primary_category.startswith(uc + "."):
            return True
    return False


def select_top_stories(
    scored_stories: list[ScoredStory],
    *,
    limit: int,
) -> list[RankedStory]:
    """
    Pure score ranking with no diversity penalties.

    Returns the globally highest-scoring stories regardless of category.
    Intended as a "breaking news" shelf — no user filtering, no diminishing returns.
    """
    sorted_stories = sorted(scored_stories, key=lambda s: s.full_score, reverse=True)
    return [_to_ranked(s, adjusted_score=s.full_score) for s in sorted_stories[:limit]]


def select_briefing(
    scored_stories: list[ScoredStory],
    *,
    user_categories: list[str] | None,
    exclude_story_ids: set[str],
    limit: int,
) -> list[RankedStory]:
    """
    Diversity-aware selection for the personalised briefing.

    - Filters to user_categories (prefix-matched) when specified.
    - Excludes story IDs already present in top_stories.
    - Applies bucket caps, entity dedup, and topic family dedup penalties.

    Selection is greedy: each iteration finds the highest adjusted-score
    candidate across all eligible buckets, then updates penalty state.
    """
    pool = [
        s for s in scored_stories
        if s.candidate.story_id not in exclude_story_ids
        and _in_user_categories(s.candidate.primary_category, user_categories)
    ]

    bucketed: dict[str | None, list[ScoredStory]] = defaultdict(list)
    for s in pool:
        bucketed[get_category_bucket(s.candidate.primary_category)].append(s)

    selected: list[RankedStory] = []
    selected_story_ids: set[str] = set()
    selected_entity_ids: set[str] = set()
    selected_topic_keys: set[str] = set()
    selected_bucket_counts: dict[str | None, int] = defaultdict(int)

    while len(selected) < limit:
        best_scored: ScoredStory | None = None
        best_adjusted = -1.0
        best_bucket: str | None = None

        for bucket, stories in bucketed.items():
            cap = BUCKET_MAX_STORIES.get(bucket, DEFAULT_BUCKET_MAX_STORIES) if bucket else DEFAULT_BUCKET_MAX_STORIES
            if selected_bucket_counts[bucket] >= cap:
                continue

            bucket_best: ScoredStory | None = None
            bucket_best_adjusted = -1.0

            for s in stories:
                if s.candidate.story_id in selected_story_ids:
                    continue

                entity_id = s.candidate.primary_entity_id
                topic_key = s.candidate.primary_topic_key

                entity_penalty = REPEATED_PRIMARY_ENTITY_PENALTY if (entity_id and entity_id in selected_entity_ids) else 1.0
                topic_penalty = REPEATED_TOPIC_PENALTY if (topic_key and topic_key in selected_topic_keys) else 1.0
                bucket_penalty = BUCKET_REPEAT_PENALTY ** selected_bucket_counts[bucket]

                adjusted = s.full_score * entity_penalty * topic_penalty * bucket_penalty

                if adjusted < MIN_ADJUSTED_SCORE:
                    continue

                if adjusted > bucket_best_adjusted:
                    bucket_best = s
                    bucket_best_adjusted = adjusted

            if bucket_best is None:
                continue

            if bucket_best_adjusted > best_adjusted:
                best_scored = bucket_best
                best_adjusted = bucket_best_adjusted
                best_bucket = bucket

        if best_scored is None:
            break

        selected.append(_to_ranked(best_scored, adjusted_score=best_adjusted))
        selected_story_ids.add(best_scored.candidate.story_id)
        selected_bucket_counts[best_bucket] += 1

        if best_scored.candidate.primary_entity_id:
            selected_entity_ids.add(best_scored.candidate.primary_entity_id)
        if best_scored.candidate.primary_topic_key:
            selected_topic_keys.add(best_scored.candidate.primary_topic_key)

    return selected
