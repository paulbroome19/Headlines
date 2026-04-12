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
from .models import RankedStory


def get_repeated_entity_penalty(
    selected_entity_ids: set[str],
    primary_entity_id: str | None,
) -> float:
    if not primary_entity_id:
        return 1.0

    if primary_entity_id in selected_entity_ids:
        return REPEATED_PRIMARY_ENTITY_PENALTY

    return 1.0


def get_repeated_topic_penalty(
    selected_topic_keys: set[str],
    primary_topic_key: str | None,
) -> float:
    if not primary_topic_key:
        return 1.0

    if primary_topic_key in selected_topic_keys:
        return REPEATED_TOPIC_PENALTY

    return 1.0


def get_bucket_cap(bucket: str | None) -> int:
    if not bucket:
        return DEFAULT_BUCKET_MAX_STORIES
    return BUCKET_MAX_STORIES.get(bucket, DEFAULT_BUCKET_MAX_STORIES)


def get_bucket_penalty(
    selected_bucket_counts: dict[str | None, int],
    bucket: str | None,
) -> float:
    count = selected_bucket_counts[bucket]
    if count <= 0:
        return 1.0
    return BUCKET_REPEAT_PENALTY ** count


def apply_selection(
    ranked_stories: list[RankedStory],
    *,
    limit: int | None = None,
) -> list[RankedStory]:
    """
    Hybrid selector:
    - stories compete globally
    - each bucket can contribute multiple stories up to a cap
    - repeated entities and repeated topic families are penalised
    - repeated buckets are softly penalised
    - within each bucket, choose the best adjusted candidate, not just the next one
    """
    bucketed: dict[str | None, list[RankedStory]] = defaultdict(list)

    for story in ranked_stories:
        bucket = get_category_bucket(story.primary_category)
        bucketed[bucket].append(story)

    selected: list[RankedStory] = []
    selected_story_ids: set[str] = set()
    selected_entity_ids: set[str] = set()
    selected_topic_keys: set[str] = set()
    selected_bucket_counts: dict[str | None, int] = defaultdict(int)

    while True:
        if limit is not None and len(selected) >= limit:
            break

        best_story: RankedStory | None = None
        best_adjusted_score = -1.0
        best_bucket: str | None = None

        for bucket, stories in bucketed.items():
            if selected_bucket_counts[bucket] >= get_bucket_cap(bucket):
                continue

            bucket_best_story: RankedStory | None = None
            bucket_best_adjusted_score = -1.0

            for story in stories:
                if story.story_id in selected_story_ids:
                    continue

                entity_penalty = get_repeated_entity_penalty(
                    selected_entity_ids=selected_entity_ids,
                    primary_entity_id=story.primary_entity_id,
                )
                topic_penalty = get_repeated_topic_penalty(
                    selected_topic_keys=selected_topic_keys,
                    primary_topic_key=story.primary_topic_key,
                )
                bucket_penalty = get_bucket_penalty(
                    selected_bucket_counts=selected_bucket_counts,
                    bucket=bucket,
                )

                adjusted_score = (
                    story.base_score
                    * entity_penalty
                    * topic_penalty
                    * bucket_penalty
                )

                if adjusted_score < MIN_ADJUSTED_SCORE:
                    continue

                if adjusted_score > bucket_best_adjusted_score:
                    bucket_best_story = story
                    bucket_best_adjusted_score = adjusted_score

            if bucket_best_story is None:
                continue

            if bucket_best_adjusted_score > best_adjusted_score:
                best_story = bucket_best_story
                best_adjusted_score = bucket_best_adjusted_score
                best_bucket = bucket

        if best_story is None:
            break

        selected_story = RankedStory(
            story_id=best_story.story_id,
            title=best_story.title,
            base_score=best_story.base_score,
            adjusted_score=best_adjusted_score,
            last_seen_at=best_story.last_seen_at,
            article_count=best_story.article_count,
            source_count=best_story.source_count,
            primary_category=best_story.primary_category,
            primary_entity_id=best_story.primary_entity_id,
            primary_entity_name=best_story.primary_entity_name,
            primary_topic_key=best_story.primary_topic_key,
            recency_score=best_story.recency_score,
            category_weight=best_story.category_weight,
            entity_weight=best_story.entity_weight,
            source_weight=best_story.source_weight,
            cluster_weight=best_story.cluster_weight,
        )
        selected.append(selected_story)
        selected_story_ids.add(best_story.story_id)
        selected_bucket_counts[best_bucket] += 1

        if best_story.primary_entity_id:
            selected_entity_ids.add(best_story.primary_entity_id)

        if best_story.primary_topic_key:
            selected_topic_keys.add(best_story.primary_topic_key)

    return selected