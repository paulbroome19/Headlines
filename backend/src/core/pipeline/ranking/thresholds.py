"""
Threshold-per-source selection (docs/ranking-depth-design.md).

Personalisation lives in WHICH stories qualify — not in distorting the ranking.
Every story carries a coverage-driven 0–10 `normalized_score` (scorer.py). A user's
bulletin is the UNION of everything that clears its source's threshold:

  - TOP STORIES — the highest, UNIVERSAL bar, across ALL topics. "What the news
    leads with." A 9–10 story reaches everyone here, whatever its category.
  - EACH TICKED TOPIC CATEGORY — its OWN LOWER bar. Ticking business lets that
    category's 8s and 7s through — stories a Top-Stories-only listener never hears.

The preset (short/medium/detailed) slides ALL bars together — it is NOT a fixed
story count. Length is an OUTPUT of (stories that cleared × their depth): a quiet
day is genuinely shorter, a heavy day longer.

Result: qualifying stories, deduped (a story clearing via two sources plays once),
ordered by importance (normalized_score desc).
"""
from __future__ import annotations

from .config import DEFAULT_PRESET, THRESHOLDS
from .models import ScoredStory


def _matches_category(primary_category: str | None, ticked: str) -> bool:
    if not primary_category:
        return False
    return primary_category == ticked or primary_category.startswith(ticked + ".")


def _is_top_stories_slug(slug: str) -> bool:
    return slug == "top-stories" or slug.startswith("top-stories.")


def select_by_thresholds(
    scored: list[ScoredStory],
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    preset: str = DEFAULT_PRESET,
) -> list[ScoredStory]:
    """
    Return the stories that clear any of their sources' thresholds for this preset,
    deduped and ordered by importance.

    include_top_stories — the front-page source (high universal bar, all topics).
    include_categories  — ticked topic-category slugs (each its own lower bar).
                          A ticked `top-stories*` slug also enables the front page.
    """
    bars = THRESHOLDS.get(preset) or THRESHOLDS[DEFAULT_PRESET]
    ts_bar = bars["top_stories"]
    cat_bar = bars["category"]

    cats = list(include_categories or [])
    # A ticked top-stories.* slug (from the taxonomy geo axis) also turns on the
    # front page; geo-region filtering of it is the deferred pool roll-up.
    top_on = include_top_stories or any(_is_top_stories_slug(c) for c in cats)
    topic_cats = [c for c in cats if not _is_top_stories_slug(c)]

    qualifying: list[ScoredStory] = []
    seen: set[str] = set()
    for s in scored:
        sid = s.candidate.story_id
        if sid in seen:
            continue
        score = s.normalized_score
        cat = s.candidate.primary_category

        via_top = top_on and score >= ts_bar
        via_category = (
            score >= cat_bar and any(_matches_category(cat, tc) for tc in topic_cats)
        )
        if via_top or via_category:
            qualifying.append(s)
            seen.add(sid)

    # Importance order; deterministic tiebreak by story_id.
    qualifying.sort(key=lambda s: (s.normalized_score, s.candidate.story_id), reverse=True)
    return qualifying
