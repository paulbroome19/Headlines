"""
Threshold + filter-order bulletin selection.

A preset is a QUALITY BAR, not a count. Every story carries a coverage-driven 0–10
`normalized_score` (scorer.py, already lifted by curated source authority + UK lean).
Selection is two pure steps:

  1. qualify_and_order — admit every story that (a) belongs to one of the user's CHOSEN
     sources (the bare front page, a ticked `top-stories.<region>` bucket, or a ticked
     topic category) AND (b) clears the preset's bar. Order the survivors by the user's
     FILTER SEQUENCE (categories.yml order: top-stories → politics → business → … →
     sport LAST), and WITHIN a category by rank (normalized_score, UK-lean tiebreak).
     This is the full reservoir — no cap.

  2. cap_bulletin — round-robin over the categories in filter order, taking each
     category's best story before any category's Nth (balance sets the floor), up to
     PRESET_DEPTH per category and MAX_BULLETIN_STORIES overall. Capping drops the
     DEEPEST stories of the most over-represented categories first.

Personalisation lives in WHICH stories qualify and their filter order — NOT a newspaper
tier that suppressed soft categories (a football follower now gets football). Duration
is an honest OUTPUT of what clears the bar, bounded by depth + ceiling.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from core.pipeline.data.ingest.pool_taxonomy import country_weight

from .category_ranker import filter_category_index
from .config import (
    BREAKING_TAU_HOURS,
    COUNTRY_TIEBREAK_STRENGTH,
    DEFAULT_PRESET,
    FRESH_CATEGORY_RELIEF,
    MAX_BULLETIN_STORIES,
    PRESET_BAR,
    PRESET_DEPTH,
    TOP_STORIES_REGIONS,
)
from .models import ScoredStory


def _matches_category(primary_category: str | None, ticked: str) -> bool:
    if not primary_category:
        return False
    return primary_category == ticked or primary_category.startswith(ticked + ".")


def _is_top_stories_slug(slug: str) -> bool:
    return slug == "top-stories" or slug.startswith("top-stories.")


def region_adjusted_score(s: ScoredStory) -> float:
    """
    normalized_score nudged by the country_weight factor (the UK lean). High-tier
    markets (gb above the rest) are the baseline; smaller markets get a small penalty.
    Used as the WITHIN-category tiebreak so, among equally-ranked stories, a UK-edition
    one surfaces first — it never reorders across categories (filter sequence does that).
    """
    cw = country_weight(s.candidate.pool_country)
    factor = 1.0 + COUNTRY_TIEBREAK_STRENGTH * (cw - 1.0)
    return s.normalized_score * factor


def _selection_context(include_top_stories: bool, include_categories: list[str] | None):
    """Derive (front_page, regions, topic_cats) from the user's selection — shared by
    qualify_and_order and cap_bulletin so buckets are computed identically."""
    cats = list(include_categories or [])
    has_selection = bool(cats)
    front_page = ("top-stories" in cats) or (include_top_stories and not has_selection)
    regions = {
        c.split(".", 1)[1] for c in cats
        if c.startswith("top-stories.") and c.split(".", 1)[1] in TOP_STORIES_REGIONS
    }
    topic_cats = [c for c in cats if not _is_top_stories_slug(c)]
    return front_page, regions, topic_cats


def _bucket_index(cat: str | None, topic_cats: list[str]) -> int:
    """The filter-sequence bucket a qualifying story belongs to. A story that matches an
    explicitly-followed TOPIC sits in that topic's bucket (politics/business/…/sport). A
    story admitted only via the front page / a region bucket is a TOP STORY — it collapses
    into the single top-stories block (index 0), so a top-stories-region selection is one
    'Top Stories' section, not a spray across every content category."""
    if cat and any(_matches_category(cat, tc) for tc in topic_cats):
        return filter_category_index(cat)
    return filter_category_index("top-stories")


def _fresh_category_relief(s: ScoredStory, now: datetime) -> float:
    """Bar reduction for a genuinely-fresh followed-topic story, decaying ~45-min e-fold.
    A just-broken story arrives single-source (low coverage) so it sits just under the
    bar; this lets it clear a little sooner. Returns 0 for old stories, up to
    FRESH_CATEGORY_RELIEF for a brand-new one."""
    last_seen = s.candidate.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    hours_ago = max((now - last_seen).total_seconds() / 3600.0, 0.0)
    return FRESH_CATEGORY_RELIEF * math.exp(-hours_ago / BREAKING_TAU_HOURS)


def qualify_and_order(
    scored: list[ScoredStory],
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    preset: str = DEFAULT_PRESET,
    now: datetime | None = None,
) -> list[ScoredStory]:
    """
    The full qualifier reservoir: every story that matches a CHOSEN source and clears
    the preset's bar, ordered by filter sequence then rank. No cap — cap_bulletin does
    the depth/ceiling. Stories are deduped (a story clearing via several sources plays
    once). Never admits unselected content.

    include_top_stories — the bare all-topics front page. Honoured ONLY when the user
                          ticked no filters at all (a general briefing) or ticked
                          `top-stories`; a narrow selection never gets front-page hard
                          news injected.
    include_categories  — the user's ticked slugs (pick order preserved by the caller).
    """
    bar = PRESET_BAR.get(preset) or PRESET_BAR[DEFAULT_PRESET]
    reference_now = now or datetime.now(timezone.utc)
    front_page, regions, topic_cats = _selection_context(include_top_stories, include_categories)

    qualifying: list[ScoredStory] = []
    seen: set[str] = set()
    for s in scored:
        sid = s.candidate.story_id
        if sid in seen:
            continue
        score = s.normalized_score
        cat = s.candidate.primary_category
        region = s.candidate.geo_region

        via_front = front_page and score >= bar
        via_region = (region in regions) and region_adjusted_score(s) >= bar
        # Fresh followed-topic relief clears its OWN bar a little sooner (via_category only).
        cat_bar_eff = bar - _fresh_category_relief(s, reference_now)
        via_category = score >= cat_bar_eff and any(_matches_category(cat, tc) for tc in topic_cats)
        if via_front or via_region or via_category:
            qualifying.append(s)
            seen.add(sid)

    # Filter sequence (top-stories block → politics → … → sport), within-bucket by rank;
    # the UK-lean region_adjusted_score breaks ties, story_id for determinism.
    qualifying.sort(key=lambda s: (
        _bucket_index(s.candidate.primary_category, topic_cats),
        -s.normalized_score,
        -region_adjusted_score(s),
        s.candidate.story_id,
    ))
    return qualifying


def cap_bulletin(
    ordered: list[ScoredStory],
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    preset: str = DEFAULT_PRESET,
) -> list[ScoredStory]:
    """
    Balance + depth + ceiling on an already-qualified, filter-ordered list. Round-robin
    over the SELECTED-SOURCE buckets in filter order: round r gives each bucket its r-th
    story (best first), so every bucket gets its best before any gets its (r+1)-th —
    balance sets the floor. Each bucket contributes at most PRESET_DEPTH[preset]; the whole
    bulletin at most MAX_BULLETIN_STORIES. Returned in filter sequence, within-bucket by
    rank. Buckets are the SELECTED sources (top-stories, politics, …, sport) — NOT the raw
    content categories — so a top-stories-region selection is one 'Top Stories' block.
    """
    depth = PRESET_DEPTH.get(preset) or PRESET_DEPTH[DEFAULT_PRESET]
    _, _, topic_cats = _selection_context(include_top_stories, include_categories)

    groups: dict[int, list[ScoredStory]] = defaultdict(list)
    for s in ordered:  # `ordered` is already (bucket index, rank) sorted
        groups[_bucket_index(s.candidate.primary_category, topic_cats)].append(s)

    kept: list[ScoredStory] = []
    for r in range(depth):
        for idx in sorted(groups):
            if r < len(groups[idx]):
                kept.append(groups[idx][r])
                if len(kept) >= MAX_BULLETIN_STORIES:
                    break
        if len(kept) >= MAX_BULLETIN_STORIES:
            break

    kept.sort(key=lambda s: (
        _bucket_index(s.candidate.primary_category, topic_cats),
        -s.normalized_score,
        s.candidate.story_id,
    ))
    return kept
