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

from core.pipeline.data.ingest.pool_taxonomy import country_weight

from .config import (
    COUNTRY_TIEBREAK_STRENGTH,
    DEFAULT_PRESET,
    THRESHOLDS,
    TOP_STORIES_REGIONS,
)
from .models import ScoredStory


# Top Stories is the "best across everything" front page — it must never come back
# empty just because nothing cleared the (deliberately high) universal bar on a
# thin/low-coverage day. When Top Stories is explicitly selected, guarantee the
# front page fills up to this many highest-scored stories regardless of the bar.
# Aligned with the summarise budget so a full bulletin can always be assembled.
_FRONT_PAGE_MIN = 12


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
    pool.sort(key=lambda s: (region_adjusted_score(s), s.candidate.story_id), reverse=True)
    return pool


def select_by_thresholds(
    scored: list[ScoredStory],
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    preset: str = DEFAULT_PRESET,
    target_count: int | None = None,
) -> list[ScoredStory]:
    """
    Return the stories that clear any of their sources' thresholds for this preset,
    deduped, ordered by importance, and capped/backfilled to `target_count` (the
    preset's length control — Quick/Standard/Deep). The result is the top-N by score;
    length is a target, not an unstable by-product of where the bar lands.

    include_top_stories — the bare front-page source (high bar, all topics).
    include_categories  — ticked slugs. `top-stories` → front page; `top-stories.
                          <region>` → that region's bucket (geo-filtered, country-
                          weighted); anything else → a topic category (lower bar).
    target_count        — desired bulletin length; defaults to the front-page floor.
    """
    target = target_count if target_count is not None else _FRONT_PAGE_MIN
    bars = THRESHOLDS.get(preset) or THRESHOLDS[DEFAULT_PRESET]
    ts_bar = bars["top_stories"]
    cat_bar = bars["category"]

    cats = list(include_categories or [])
    # Bare "top-stories" tick OR the include_top_stories flag = the whole front page.
    front_page = include_top_stories or ("top-stories" in cats)
    # Ticked top-stories.<region> slugs enable that region's bucket (valid leaves only).
    regions = {
        c.split(".", 1)[1] for c in cats
        if c.startswith("top-stories.") and c.split(".", 1)[1] in TOP_STORIES_REGIONS
    }
    topic_cats = [c for c in cats if not _is_top_stories_slug(c)]

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
        via_category = score >= cat_bar and any(_matches_category(cat, tc) for tc in topic_cats)
        if via_front or via_region or via_category:
            qualifying.append(s)
            seen.add(sid)

    # Top Stories guarantee: never return a briefing empty/thin just because nothing
    # cleared the high universal bar — backfill with the highest-scored stories (which
    # ARE the top stories) up to a full-bulletin floor. The app sends
    # include_top_stories=true on every request, so honouring the flag here means
    # EVERY briefing gets this floor and is never empty while stories exist — an empty
    # briefing reads as broken. A truly empty candidate set still yields nothing.
    top_stories_selected = include_top_stories or ("top-stories" in cats) or bool(regions)
    if top_stories_selected and len(qualifying) < target:
        for s in sorted(scored, key=lambda x: x.normalized_score, reverse=True):
            sid = s.candidate.story_id
            if sid in seen:
                continue
            qualifying.append(s)
            seen.add(sid)
            if len(qualifying) >= target:
                break

    # Coverage-primary order (so a hugely-covered story leads regardless of country);
    # country_weight is the secondary tiebreak; story_id keeps it deterministic.
    qualifying.sort(
        key=lambda s: (s.normalized_score, region_adjusted_score(s), s.candidate.story_id),
        reverse=True,
    )
    # Cap to the preset's target length — the top-N by score. (Topic-only briefings that
    # genuinely have fewer qualifiers stay short; a truly empty set stays empty.)
    qualifying = qualifying[:target]
    return qualifying
