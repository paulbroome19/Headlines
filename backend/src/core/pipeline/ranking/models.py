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
    # Regional roll-up (Part 4): dominant geo_region + country of the story's
    # coverage (from the pool stamp). None for legacy/un-stamped stories.
    geo_region: str | None = None
    pool_country: str | None = None
    # Best (lowest-numbered = most credible) source tier among this story's outlets
    # (curated source_credibility tiers). Default = unknown. Kept for now-playing
    # attribution / back-compat.
    top_source_tier: int = 9
    # The story's raw outlet display names — carried through so the ranker can apply
    # per-outlet, per-category authority + the region-gated regional whitelist
    # (source_credibility.resolve_authority). Empty for legacy candidates.
    sources: tuple[str, ...] = ()
    # TOP-STORY flag — set by the editorial LLM pass as a SEPARATE significance judgment,
    # ON TOP of primary_category (which stays what the story IS). True only for genuinely
    # significant news (war, disasters, major crime, serious government action, major
    # accidents/controversies) — never soft-category stuff (tech/gaming/celebrity/routine
    # business) even if high-scoring. The front-page/Top-Stories block admits ONLY these.
    top_story: bool = False


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
    # Day-level importance model (coverage-driven; see docs/ranking-depth-design.md).
    importance: float = 0.0        # coverage · prominence · freshness · category_tiebreak · source_authority
    normalized_score: float = 0.0  # importance mapped to 0–10 (the threshold axis)
    # True if a trusted/active outlet makes this story eligible for a lead slot
    # (global tier / specialist / region-active regional). Unknown-only or
    # excluded-only stories are False → supplementary, never lead. Region-gated
    # eligibility is finalised in thresholds.py once selected regions are known.
    lead_eligible: bool = False


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
    # Day-level importance model (coverage-driven).
    importance: float = 0.0
    normalized_score: float = 0.0   # 0–10 — the axis thresholds/depth work on


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
