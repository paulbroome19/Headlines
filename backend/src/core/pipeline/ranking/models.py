from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class StoryRankingCandidate:
    story_id: str
    title: str
    last_seen_at: datetime
    article_count: int
    source_count: int
    primary_category: str | None
    primary_entity_id: str | None
    primary_entity_name: str | None
    primary_entity_weight: float | None
    primary_topic_key: str | None


@dataclass(slots=True)
class RankedStory:
    story_id: str
    title: str
    base_score: float
    adjusted_score: float
    last_seen_at: datetime
    article_count: int
    source_count: int
    primary_category: str | None
    primary_entity_id: str | None
    primary_entity_name: str | None
    primary_topic_key: str | None
    recency_score: float
    category_weight: float
    entity_weight: float
    source_weight: float
    cluster_weight: float