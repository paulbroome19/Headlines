from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from .config import (
    CATEGORY_TIERS,
    CATEGORY_WEIGHTS,
    DEFAULT_CATEGORY_WEIGHT,
    DEFAULT_TIER,
)


@lru_cache(maxsize=1)
def _filter_order() -> dict[str, int]:
    """Map each TOP-LEVEL category slug → its position in the picker (the categories.yml
    key order, which the iOS picker renders): top-stories, politics, business, technology,
    culture, science, health, environment, sport. Derived from the single canonical
    source so it can never drift from the picker."""
    from core.pipeline.data.normalise.categorise.category_loader import load_category_groups
    return {g["slug"]: i for i, g in enumerate(load_category_groups())}


def filter_category_index(category_slug: str | None) -> int:
    """The bulletin RUNNING-ORDER position for a story's category: the index of its
    TOP-LEVEL group in the filter sequence (top-stories first … sport last). e.g.
    top-stories.us / politics.world / sport.football.premier-league → 0 / 1 / 8.
    Unknown categories sort to the end."""
    top = (category_slug or "").split(".", 1)[0]
    return _filter_order().get(top, 10_000)


def get_category_tier(category_slug: str | None) -> int:
    """
    Returns the newspaper tier for a category slug (1 = front page, 2 = middle,
    3 = back). Resolution mirrors get_category_weight: exact match, then prefix
    fallback (strip the rightmost segment), then DEFAULT_TIER.

    e.g. sport.football.premier-league -> sport (3); politics.world -> politics (1);
    top-stories.middle-east -> top-stories (1).
    """
    if not category_slug:
        return DEFAULT_TIER

    if category_slug in CATEGORY_TIERS:
        return CATEGORY_TIERS[category_slug]

    parts = category_slug.split(".")
    while len(parts) > 1:
        parts.pop()
        fallback = ".".join(parts)
        if fallback in CATEGORY_TIERS:
            return CATEGORY_TIERS[fallback]

    return DEFAULT_TIER


def get_category_weight(category_slug: str | None) -> float:
    """
    Returns the editorial weight for a category slug.

    Resolution order:
    1. Exact match in CATEGORY_WEIGHTS
    2. Prefix fallback — strip the rightmost segment until a match is found
       (e.g. sport.football.premier-league -> sport.football -> sport)
    3. DEFAULT_CATEGORY_WEIGHT
    """
    if not category_slug:
        return DEFAULT_CATEGORY_WEIGHT

    if category_slug in CATEGORY_WEIGHTS:
        return CATEGORY_WEIGHTS[category_slug]

    parts = category_slug.split(".")
    while len(parts) > 1:
        parts.pop()
        fallback = ".".join(parts)
        if fallback in CATEGORY_WEIGHTS:
            return CATEGORY_WEIGHTS[fallback]

    return DEFAULT_CATEGORY_WEIGHT


def rank_categories(slugs: Iterable[str]) -> list[str]:
    """
    Returns a deduplicated list of category slugs sorted by editorial weight
    descending. Order is always stable and config-driven — never dynamic.
    """
    seen: dict[str, float] = {}
    for slug in slugs:
        if slug not in seen:
            seen[slug] = get_category_weight(slug)
    return sorted(seen, key=lambda s: seen[s], reverse=True)
