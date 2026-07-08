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
    TOP_STORIES_REGION_ORDER,
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


def _subject_region(primary_category: str | None) -> str:
    """SUBJECT region for top-block ordering, derived deterministically from the primary_category
    prefix: politics.uk → uk, politics.us → us, politics.europe → europe; everything else
    (politics.world AND every non-politics category) → world.

    Replaces the old geo_region-based lean. geo_region is SOURCE region and UK-saturated, so it
    could not tell a UK-subject story from a global one. LIMITATION: only politics.* carries a
    subject region from the prefix, so a UK-subject business/health top story sorts as 'world'
    until the editorial region emission (post-demo geographic model) lands."""
    c = primary_category or ""
    if c == "politics.uk" or c.startswith("politics.uk."):
        return "uk"
    if c == "politics.us" or c.startswith("politics.us."):
        return "us"
    if c == "politics.europe" or c.startswith("politics.europe."):
        return "europe"
    return "world"


def _region_rank(region: str) -> int:
    """Position of a subject region in the configured order (an unlisted region sorts last)."""
    try:
        return TOP_STORIES_REGION_ORDER.index(region)
    except ValueError:
        return len(TOP_STORIES_REGION_ORDER)


def _top_block_rank(s: ScoredStory, bucket: int, top_idx: int) -> tuple[int, float]:
    """Within-bucket sort sub-key. In the TOP STORIES block, order by SUBJECT-region group first
    (TOP_STORIES_REGION_ORDER — UK leads a UK app), then by score within the group. For every
    other (topic) bucket the region component is a constant, so ordering is by score exactly as
    before. A tuple (region_rank, -score) so it slots straight into the order key."""
    if bucket == top_idx:
        return (_region_rank(_subject_region(s.candidate.primary_category)), -s.normalized_score)
    return (0, -s.normalized_score)


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


def _bucket_index(cat: str | None, top_story: bool, geo_region: str | None,
                  topic_cats: list[str], front_page: bool, regions: set[str]) -> int:
    """The filter-sequence bucket a qualifying story belongs to. A genuine TOP STORY (the
    editorial top_story flag AND the user picked the bare front page or this story's region)
    collapses into the single top-stories block (index 0) — so a story that is BOTH a top
    story and a followed topic plays once, in Top Stories. Otherwise a story matching an
    explicitly-followed TOPIC sits in that topic's bucket (politics/business/…/sport).
    Takes primitives (not a ScoredStory) so the daily-edition path — which orders stored ids
    without live scored objects — can call it identically."""
    is_top = top_story and (front_page or (geo_region in regions))
    if is_top:
        return filter_category_index("top-stories")
    if cat and any(_matches_category(cat, tc) for tc in topic_cats):
        return filter_category_index(cat)
    return filter_category_index("top-stories")


def _bucket_of(s: ScoredStory, topic_cats: list[str], front_page: bool, regions: set[str]) -> int:
    return _bucket_index(s.candidate.primary_category, s.candidate.top_story,
                         s.candidate.geo_region, topic_cats, front_page, regions)


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

        # TOP STORIES are a SIGNIFICANCE judgment (the editorial top_story flag), NOT "any story
        # clearing the bar". The front page and the region blocks admit ONLY flagged top stories,
        # in full (no coverage bar) — so a high-coverage tech/gaming/celebrity roundup can never
        # lead. Topic filters are unchanged (score must still clear the preset bar).
        via_front = front_page and s.candidate.top_story
        via_region = (region in regions) and s.candidate.top_story
        # Fresh followed-topic relief clears its OWN bar a little sooner (via_category only).
        cat_bar_eff = bar - _fresh_category_relief(s, reference_now)
        via_category = score >= cat_bar_eff and any(_matches_category(cat, tc) for tc in topic_cats)
        if via_front or via_region or via_category:
            qualifying.append(s)
            seen.add(sid)

    # Filter sequence (top-stories block → politics → … → sport), within-bucket by rank; in the
    # Top Stories block ordering is by SUBJECT region group (UK first) then score, so UK top
    # stories lead a UK front page; the country_weight region_adjusted_score breaks remaining
    # ties, story_id for determinism.
    top_idx = filter_category_index("top-stories")

    def _order_key(s: ScoredStory):
        b = _bucket_of(s, topic_cats, front_page, regions)
        return (b, _top_block_rank(s, b, top_idx), -region_adjusted_score(s), s.candidate.story_id)

    qualifying.sort(key=_order_key)
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
    front_page, regions, topic_cats = _selection_context(include_top_stories, include_categories)

    groups: dict[int, list[ScoredStory]] = defaultdict(list)
    for s in ordered:  # `ordered` is already (bucket index, rank) sorted
        groups[_bucket_of(s, topic_cats, front_page, regions)].append(s)

    top_idx = filter_category_index("top-stories")
    kept: list[ScoredStory] = []
    # TOP STORIES: every flagged top story, IN FULL — they bypass balance/depth/ceiling.
    kept.extend(groups.get(top_idx, []))
    # THE REST: the existing balanced round-robin over the OTHER selected buckets, filling up to
    # the overall ceiling (top stories already kept don't consume the topic buckets' depth).
    other = {idx: items for idx, items in groups.items() if idx != top_idx}
    for r in range(depth):
        for idx in sorted(other):
            if r < len(other[idx]):
                kept.append(other[idx][r])
                if len(kept) >= MAX_BULLETIN_STORIES:
                    break
        if len(kept) >= MAX_BULLETIN_STORIES:
            break

    kept.sort(key=lambda s: (
        (b := _bucket_of(s, topic_cats, front_page, regions)),
        _top_block_rank(s, b, top_idx),
        s.candidate.story_id,
    ))
    return kept
