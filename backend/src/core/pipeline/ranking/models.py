from __future__ import annotations

from dataclasses import dataclass, field
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
class ScoredStory:
    """
    Intermediate representation produced by scorer.py.
    Holds all component signals before selection is applied.
    """
    candidate: StoryRankingCandidate
    within_category_score: float  # recency × entity × source × cluster (no category weight)
    full_score: float             # within_category_score × category_weight
    category_weight: float
    recency_score: float
    entity_weight: float
    source_weight: float
    cluster_weight: float


@dataclass(slots=True)
class RankedStory:
    """
    Final per-story output. Carries both scores and all component weights
    for transparency / debugging.
    """
    story_id: str
    title: str
    full_score: float        # scorer output × category weight (pre-selection)
    adjusted_score: float    # full_score after diversity penalties (post-selection)
    within_category_score: float
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


@dataclass
class RankingResult:
    """
    Dual output produced by StoryRanker.rank().

    top_stories — globally highest-scoring stories, no category filtering,
                  no diversity penalties. Intended as a "breaking news" shelf.

    briefing    — stories filtered to user's selected categories with
                  diminishing returns applied and top_stories excluded.
                  Intended as the main personalised bulletin.
    """
    top_stories: list[RankedStory] = field(default_factory=list)
    briefing: list[RankedStory] = field(default_factory=list)
