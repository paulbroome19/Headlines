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
    TOP_BLOCK_SIZE,
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
    """Derive (toggled_regions, topic_cats) from the user's selection — shared by qualify_and_order
    and cap_bulletin so the top block and the topic buckets are computed identically.

    toggled_regions: the SUBJECT regions turned on under Top Stories (the `top-stories.<region>`
    slugs, restricted to the four valid regions). The bare `top-stories` slug — or
    include_top_stories with no explicit selection (a general briefing) — means ALL regions.
    Empty set → Top Stories is OFF (no block).
    topic_cats: the followed topic slugs (everything that is not a top-stories slug)."""
    cats = list(include_categories or [])
    toggled_regions = {
        c.split(".", 1)[1] for c in cats
        if c.startswith("top-stories.") and c.split(".", 1)[1] in TOP_STORIES_REGIONS
    }
    if ("top-stories" in cats) or (include_top_stories and not cats):
        toggled_regions = set(TOP_STORIES_REGIONS)   # bare / general briefing → every region
    topic_cats = [c for c in cats if not _is_top_stories_slug(c)]
    return toggled_regions, topic_cats


def _is_top_candidate(cat: str | None, top_story: bool, toggled_regions: set[str]) -> bool:
    """Eligible for the Top Stories block iff editorially flagged AND its SUBJECT region (from the
    category prefix) is one the user toggled. geo_region is never consulted — it is source region
    and UK-saturated, so it can't tell a UK-subject story from a global one."""
    return bool(top_story) and _subject_region(cat) in toggled_regions


def _matches_any_topic(cat: str | None, topic_cats: list[str]) -> bool:
    return bool(cat) and any(_matches_category(cat, tc) for tc in topic_cats)


def _bucket_index(cat: str | None, top_story: bool, topic_cats: list[str],
                  toggled_regions: set[str]) -> int:
    """Filter-sequence bucket a story belongs to: a top-block candidate collapses into the single
    top-stories block (index 0) — so a story that is BOTH a top story and a followed topic plays
    once, in Top Stories; otherwise a followed-topic story sits in its topic bucket. (Whether an
    eligible candidate is actually KEPT in the block — the TOP_BLOCK_SIZE cap and backfill — is
    decided in cap_bulletin.)"""
    if _is_top_candidate(cat, top_story, toggled_regions):
        return filter_category_index("top-stories")
    if _matches_any_topic(cat, topic_cats):
        return filter_category_index(cat)
    return filter_category_index("top-stories")


def _bucket_of(s: ScoredStory, topic_cats: list[str], toggled_regions: set[str]) -> int:
    return _bucket_index(s.candidate.primary_category, s.candidate.top_story,
                         topic_cats, toggled_regions)


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
    the block size / depth / ceiling. Stories are deduped. Never admits unselected content.

    A story is admitted iff it is EITHER a Top Stories candidate (editorially flagged AND its
    subject region is toggled — bar-bypass, they lead the front page) OR a followed-topic story
    that clears the preset bar. A flagged story whose region is NOT toggled can still appear, but
    only as an ordinary topic story via the bar (never in the block).
    include_categories — the user's ticked slugs (top-stories.<region> + followed topics).
    """
    bar = PRESET_BAR.get(preset) or PRESET_BAR[DEFAULT_PRESET]
    reference_now = now or datetime.now(timezone.utc)
    toggled_regions, topic_cats = _selection_context(include_top_stories, include_categories)

    qualifying: list[ScoredStory] = []
    seen: set[str] = set()
    for s in scored:
        sid = s.candidate.story_id
        if sid in seen:
            continue
        cat = s.candidate.primary_category
        via_top = _is_top_candidate(cat, s.candidate.top_story, toggled_regions)  # flagged + toggled region
        cat_bar_eff = bar - _fresh_category_relief(s, reference_now)  # fresh followed-topic relief
        via_category = s.normalized_score >= cat_bar_eff and _matches_any_topic(cat, topic_cats)
        if via_top or via_category:
            qualifying.append(s)
            seen.add(sid)

    # Filter sequence (top-stories block → politics → … → sport), within-bucket by rank; in the Top
    # Stories block ordering is by SUBJECT region group (UK first) then score; region_adjusted_score
    # (country_weight) breaks remaining ties, story_id for determinism.
    top_idx = filter_category_index("top-stories")

    def _order_key(s: ScoredStory):
        b = _bucket_of(s, topic_cats, toggled_regions)
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
    Compose the final bulletin (the FINAL top-stories spec). Returns, in play/display order:

      1. THE TOP BLOCK — the TOP_BLOCK_SIZE highest-ranked flagged stories across the user's
         TOGGLED regions (pure rank for membership; no per-region caps), presented grouped by
         region (TOP_STORIES_REGION_ORDER, filtered to toggled) then by rank within a region.
      2. BACKFILL — if fewer than TOP_BLOCK_SIZE flagged stories are available, the spare slots
         fill with the highest-ranked FOLLOWED-TOPIC stories, presented in topic (filter) order,
         appended after the region groups. Backfilled stories are ordinary stories in top
         position and are consumed (they do not repeat in their topic section).
      3. THE TOPIC SECTIONS — the remaining followed-topic stories, round-robin over the topic
         buckets (balance) up to PRESET_DEPTH per bucket and MAX_BULLETIN_STORIES overall,
         presented in filter sequence, within-bucket by rank.

    Top Stories OFF (no toggled region) → no block, no backfill: just the topic sections.
    Everything here is drawn ONLY from the user's toggled regions + followed topics — nothing
    outside their filters ever appears.
    """
    depth = PRESET_DEPTH.get(preset) or PRESET_DEPTH[DEFAULT_PRESET]
    toggled_regions, topic_cats = _selection_context(include_top_stories, include_categories)
    top_block_on = bool(toggled_regions)

    def _rank_key(s: ScoredStory):
        return (-s.normalized_score, s.candidate.story_id)

    # ── 1. TOP BLOCK: membership by pure rank across toggled regions; present by region group ──
    block: list[ScoredStory] = []
    block_ids: set[str] = set()
    if top_block_on:
        candidates = [s for s in ordered
                      if _is_top_candidate(s.candidate.primary_category, s.candidate.top_story, toggled_regions)]
        chosen = sorted(candidates, key=_rank_key)[:TOP_BLOCK_SIZE]
        block = sorted(chosen, key=lambda s: (_region_rank(_subject_region(s.candidate.primary_category)),
                                              _rank_key(s)))
        block_ids = {s.candidate.story_id for s in block}

    # Topic pool: followed-topic stories not already in the block (includes flagged overflow —
    # a flagged story that didn't make the top-N still appears in its section if it's a followed
    # topic), grouped by topic bucket, each bucket by rank.
    topic_by_bucket: dict[int, list[ScoredStory]] = defaultdict(list)
    for s in ordered:
        if s.candidate.story_id in block_ids:
            continue
        if _matches_any_topic(s.candidate.primary_category, topic_cats):
            topic_by_bucket[filter_category_index(s.candidate.primary_category)].append(s)
    for items in topic_by_bucket.values():
        items.sort(key=_rank_key)

    # ── 2. BACKFILL spare top-block slots from the highest-ranked followed-topic stories ──
    backfill: list[ScoredStory] = []
    if top_block_on and len(block) < TOP_BLOCK_SIZE:
        pool = [s for items in topic_by_bucket.values() for s in items]
        chosen_bf = sorted(pool, key=_rank_key)[:TOP_BLOCK_SIZE - len(block)]
        backfill = sorted(chosen_bf, key=lambda s: (filter_category_index(s.candidate.primary_category),
                                                    _rank_key(s)))
        bf_ids = {s.candidate.story_id for s in backfill}
        for idx in list(topic_by_bucket):  # consume — don't repeat in a section
            topic_by_bucket[idx] = [s for s in topic_by_bucket[idx] if s.candidate.story_id not in bf_ids]

    top_section = block + backfill

    # ── 3. THE REST: round-robin the topic buckets up to depth + the remaining ceiling ──
    rest: list[ScoredStory] = []
    ceiling = MAX_BULLETIN_STORIES - len(top_section)
    for r in range(depth):
        for idx in sorted(topic_by_bucket):
            if r < len(topic_by_bucket[idx]):
                rest.append(topic_by_bucket[idx][r])
                if len(rest) >= ceiling:
                    break
        if len(rest) >= ceiling:
            break
    rest.sort(key=lambda s: (filter_category_index(s.candidate.primary_category), _rank_key(s)))

    return top_section + rest
