"""
Threshold-per-source selection (docs/ranking-depth-design.md) + Top-Stories-by-
region roll-up (docs/ingestion-pool-design.md Part 4).

Personalisation lives in WHICH stories qualify — not in distorting the ranking.
Every story carries a coverage-driven 0–10 `normalized_score` (scorer.py). A user's
bulletin is the UNION of everything that clears its source's threshold:

  - TOP STORIES — the highest, UNIVERSAL bar.
      • the bare front page (all topics), or
      • a regional bucket `top-stories.<region>` — only stories geo-stamped to that
        region, ranked with a LIGHT `country_weight` tiebreak so a big story from a
        top-tier market (gb/us/…) beats a minor one from a small country. Coverage
        still wins — a hugely-covered small-country story leads regardless.
  - EACH TICKED TOPIC CATEGORY — its OWN LOWER bar.

The preset (short/medium/detailed) slides ALL bars together. Result: qualifying
stories, deduped ACROSS buckets (a story clearing via a topic filter AND a regional
bucket plays once), ordered by importance (coverage), with country_weight as a
secondary tiebreak.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from core.pipeline.data.ingest.pool_taxonomy import country_weight

from .category_ranker import get_category_tier
from .config import (
    BREAKING_TAU_HOURS,
    BREAKTHROUGH_SOURCES,
    COUNTRY_TIEBREAK_STRENGTH,
    DEFAULT_PRESET,
    FRESH_CATEGORY_RELIEF,
    THRESHOLDS,
    TOP_STORIES_REGIONS,
)
from .models import ScoredStory


def effective_tier(s: ScoredStory) -> int:
    """
    The newspaper tier that governs a story's front-page position (1 = front, 2 =
    middle, 3 = back). It is the CATEGORY's base tier — EXCEPT a Tier 2/3 story whose
    distinct-source coverage is exceptional (>= BREAKTHROUGH_SOURCES) breaks through
    to the front page (Tier 1), because the whole press led with it (a World Cup
    final). Tier is DECISIVE: every Tier-1 story orders ahead of every Tier-2, ahead
    of every Tier-3; coverage only orders stories WITHIN a tier (and earns the rare
    break-through). Ordinary sport/tech clustering (≈8–11 sources) never clears the
    bar, so soft news never leads unless it is genuinely dominating the news cycle.
    """
    base = get_category_tier(s.candidate.primary_category)
    if base > 1 and s.candidate.source_count >= BREAKTHROUGH_SOURCES:
        return 1
    return base


# Top Stories is the "best across everything" front page — it must never come back
# empty just because nothing cleared the (deliberately high) universal bar on a
# thin/low-coverage day. When Top Stories is explicitly selected, guarantee the
# front page fills up to this many highest-scored stories regardless of the bar.
# Aligned with the summarise budget so a full bulletin can always be assembled.
_FRONT_PAGE_MIN = 12


# Guaranteed representation across selected filters. Selecting a filter is a PROMISE to
# include it, so a busy category must never rank a chosen one down to zero: every
# selected filter contributes at least this many of its top stories before the remaining
# slots are filled by global rank. Length controls how many PER filter, not WHICH filters
# appear — longer presets guarantee a little more depth per filter.
_FLOOR_PER_FILTER = {"short": 1, "medium": 1, "detailed": 2}
_DEFAULT_FLOOR = 1


def _matches_category(primary_category: str | None, ticked: str) -> bool:
    if not primary_category:
        return False
    return primary_category == ticked or primary_category.startswith(ticked + ".")


def _is_top_stories_slug(slug: str) -> bool:
    return slug == "top-stories" or slug.startswith("top-stories.")


def region_adjusted_score(s: ScoredStory) -> float:
    """
    normalized_score nudged by a LIGHT country_weight factor (≈ ±10%). High-tier
    markets are the baseline (factor ≈ 1.0); smaller markets get a small penalty.
    Because the factor is light and ≤ 1.0, coverage always dominates — a
    hugely-covered small-country story still outranks a mid-covered big-market one.
    """
    cw = country_weight(s.candidate.pool_country)
    factor = 1.0 + COUNTRY_TIEBREAK_STRENGTH * (cw - 1.0)
    return s.normalized_score * factor


def _fresh_category_relief(s: ScoredStory, now: datetime) -> float:
    """Category-bar reduction for a genuinely-fresh story, decaying ~45-min e-fold.
    Used by the via_category branch ONLY — never the front-page/region bars. Returns
    0 for old stories, up to FRESH_CATEGORY_RELIEF for a brand-new one."""
    last_seen = s.candidate.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    hours_ago = max((now - last_seen).total_seconds() / 3600.0, 0.0)
    return FRESH_CATEGORY_RELIEF * math.exp(-hours_ago / BREAKING_TAU_HOURS)


def rank_region(
    scored: list[ScoredStory],
    region: str,
    *,
    preset: str = DEFAULT_PRESET,
) -> list[ScoredStory]:
    """
    The pure regional roll-up: a region's front page. Filter to stories geo-stamped
    to `region`, keep those clearing the Top-Stories bar (country-adjusted), and
    order by the country-weighted score. Reuses the coverage-driven scorer.
    """
    bars = THRESHOLDS.get(preset) or THRESHOLDS[DEFAULT_PRESET]
    ts_bar = bars["top_stories"]
    pool = [s for s in scored if s.candidate.geo_region == region and region_adjusted_score(s) >= ts_bar]
    # Within-tier order: country-weighted coverage (as before).
    pool.sort(key=lambda s: (region_adjusted_score(s), s.candidate.story_id), reverse=True)
    # Tier is decisive — a region's front page leads with its hard news, soft news
    # trails. Stable sort preserves the coverage order within each tier.
    pool.sort(key=effective_tier)
    return pool


def select_by_thresholds(
    scored: list[ScoredStory],
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    preset: str = DEFAULT_PRESET,
    target_count: int | None = None,
    now: datetime | None = None,
) -> list[ScoredStory]:
    """
    Return the stories that clear any of their sources' thresholds for this preset,
    deduped and length-controlled to `target_count` (Quick/Standard/Deep).

    The briefing only ever contains stories matching the user's CHOSEN sources; the
    newspaper tiering orders within that set, it never reaches outside it.

    GUARANTEED REPRESENTATION: every selected filter with qualifying content appears —
    selecting a filter is a promise to include it, so a busy category can never rank a
    chosen one down to zero. Each filter is guaranteed its top `_FLOOR_PER_FILTER[preset]`
    stories; the remaining slots fill by global rank (so depth follows importance — the
    busiest category dominates the remainder). Length controls how many PER filter, not
    which filters appear. A filter with no content is simply absent.

    include_top_stories — the DEFAULT-only front-page flag. It admits the bare all-
                          topics front page ONLY when the user ticked no filters at all
                          (a general briefing). It is NOT a blanket override: with a
                          narrow topic selection it does nothing, so Football-only never
                          gets front-page hard news injected.
    include_categories  — ticked slugs = the user's selection. `top-stories` → the
                          front page (all topics); `top-stories.<region>` → that
                          region's bucket (geo-filtered, country-weighted); anything
                          else → a topic category (lower bar). Top-Stories content
                          appears ONLY when top-stories/`top-stories.<region>` is here.
    target_count        — desired bulletin length; defaults to the front-page floor.
    now                 — reference time for the fresh-category relief (injectable
                          for tests); defaults to now(UTC).
    """
    target = target_count if target_count is not None else _FRONT_PAGE_MIN
    reference_now = now or datetime.now(timezone.utc)
    bars = THRESHOLDS.get(preset) or THRESHOLDS[DEFAULT_PRESET]
    ts_bar = bars["top_stories"]
    cat_bar = bars["category"]

    cats = list(include_categories or [])
    has_selection = bool(cats)
    # The bare all-topics front page. Selected EXPLICITLY by ticking "top-stories", or
    # IMPLICITLY when the user picked no filters at all (default = the general briefing).
    # The legacy `include_top_stories` flag is NO LONGER a blanket override: the app
    # sends it true on EVERY request, so honouring it unconditionally injected front-page
    # hard news into every narrow selection (a Football-only briefing filled with
    # Iran/tariffs/Kroger and zero football). Top-Stories content now appears ONLY when
    # the user actually chose it — the newspaper tiering orders WITHIN their selection,
    # it never reaches outside it.
    front_page = ("top-stories" in cats) or (include_top_stories and not has_selection)
    # Ticked top-stories.<region> slugs enable that region's bucket (valid leaves only).
    regions = {
        c.split(".", 1)[1] for c in cats
        if c.startswith("top-stories.") and c.split(".", 1)[1] in TOP_STORIES_REGIONS
    }
    topic_cats = [c for c in cats if not _is_top_stories_slug(c)]

    def _matches_selection(s: ScoredStory) -> bool:
        """True when a story belongs to one of the user's CHOSEN sources: the front
        page (all topics), a chosen region bucket, or a ticked topic category. Scopes
        the never-empty backfill so it can only ever pull stories the user actually
        asked for — never unselected hard news padded in to hit the target length."""
        if front_page:
            return True
        if s.candidate.geo_region in regions:
            return True
        cat = s.candidate.primary_category
        return any(_matches_category(cat, tc) for tc in topic_cats)

    qualifying: list[ScoredStory] = []
    seen: set[str] = set()
    for s in scored:
        sid = s.candidate.story_id
        if sid in seen:
            continue
        score = s.normalized_score
        cat = s.candidate.primary_category
        region = s.candidate.geo_region

        via_front = front_page and score >= ts_bar
        via_region = (region in regions) and region_adjusted_score(s) >= ts_bar
        # Fresh followed-topic relief: a genuinely-breaking story clears its OWN
        # (already-lower) category bar a little sooner. via_category ONLY — via_front
        # / via_region above are untouched, so front-page composition and the final
        # coverage-first sort are unchanged; a fresh single-source story fills a tail
        # slot, it never leapfrogs a bigger one.
        cat_bar_eff = cat_bar - _fresh_category_relief(s, reference_now)
        via_category = score >= cat_bar_eff and any(_matches_category(cat, tc) for tc in topic_cats)
        if via_front or via_region or via_category:
            qualifying.append(s)
            seen.add(sid)

    # Never-empty guarantee, STRICTLY SCOPED TO THE SELECTION. The front page (and a
    # regional bucket) promises "the best across everything / across the region" — it
    # must not come back thin just because little cleared the deliberately-high universal
    # bar, so backfill with the highest-scored stories up to the target. Crucially the
    # backfill pool is `_matches_selection` ONLY: a front-page/default selection means
    # all topics (so any story is fair game — that IS the front page), a region selection
    # fills only from that region. A pure TOPIC selection (e.g. Football only) does NOT
    # enter this branch at all — it stays its natural length, simply SHORTER (or empty on
    # a thin day), and is NEVER padded with unselected categories.
    if (front_page or regions) and len(qualifying) < target:
        for s in sorted(scored, key=region_adjusted_score, reverse=True):
            if not _matches_selection(s):
                continue
            sid = s.candidate.story_id
            if sid in seen:
                continue
            qualifying.append(s)
            seen.add(sid)
            if len(qualifying) >= target:
                break

    # ── Guaranteed representation across selected filters ────────────────────────
    # `qualifying` now holds everything eligible. Selecting a filter is a PROMISE to
    # include it, so a busy category (politics) must not rank a chosen one (football) down
    # to zero. Assign each story to its MOST-SPECIFIC chosen filter, guarantee each such
    # filter its top `floor` stories, then fill the REMAINING slots by global rank across
    # the whole pool. Only the floor is equal — depth still follows importance (the
    # busiest category dominates the remainder). A filter with no content is simply absent;
    # a single filter (or the bare front page) reduces to pure rank.
    def _bucket_key(s: ScoredStory) -> tuple[str, str] | None:
        cat = s.candidate.primary_category
        matching = [tc for tc in topic_cats if _matches_category(cat, tc)]
        if matching:
            return ("topic", max(matching, key=len))       # most specific ticked topic
        if s.candidate.geo_region in regions:
            return ("region", s.candidate.geo_region)
        if front_page:
            return ("front", "")                            # catch-all front page
        return None

    def _by_score(s: ScoredStory) -> tuple[float, float, str]:
        return (s.normalized_score, region_adjusted_score(s), s.candidate.story_id)

    buckets: dict[tuple[str, str], list[ScoredStory]] = {}
    for s in qualifying:
        key = _bucket_key(s)
        if key is not None:
            buckets.setdefault(key, []).append(s)
    for b in buckets.values():
        b.sort(key=_by_score, reverse=True)

    floor = _FLOOR_PER_FILTER.get(preset, _DEFAULT_FLOOR)
    # Order filters by their top story's importance so, if the floors can't all fit the
    # target (more filters than slots), the most important filters win the scarce slots.
    ordered_keys = sorted(
        buckets, key=lambda k: _by_score(buckets[k][0]), reverse=True
    )

    selected: list[ScoredStory] = []
    picked: set[str] = set()
    # Phase 1 — guaranteed floor: up to `floor` top stories from each chosen filter.
    for rank_i in range(floor):
        if len(selected) >= target:
            break
        for key in ordered_keys:
            if len(selected) >= target:
                break
            bucket = buckets[key]
            if len(bucket) > rank_i:
                s = bucket[rank_i]
                if s.candidate.story_id not in picked:
                    selected.append(s)
                    picked.add(s.candidate.story_id)
    # Phase 2 — fill the rest by global rank across the whole pool.
    if len(selected) < target:
        for s in sorted(qualifying, key=_by_score, reverse=True):
            if len(selected) >= target:
                break
            if s.candidate.story_id not in picked:
                selected.append(s)
                picked.add(s.candidate.story_id)

    # Newspaper order on the chosen set: within-tier by coverage (country_weight is the
    # secondary tiebreak; story_id keeps it deterministic), THEN by tier, which is
    # DECISIVE (stable sort preserves the coverage order inside each tier). The front page
    # (Tier 1) leads, middle (Tier 2) and back (Tier 3: sport/tech/culture) follow —
    # unless a soft story broke through on exceptional coverage (see effective_tier).
    # Tiering decides ORDER and per-story depth, never whether a selected filter appears.
    selected.sort(key=_by_score, reverse=True)
    selected.sort(key=effective_tier)
    return selected[:target]
