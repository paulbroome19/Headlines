from __future__ import annotations

from typing import Iterable

from .config import CATEGORY_WEIGHTS, DEFAULT_CATEGORY_WEIGHT


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
