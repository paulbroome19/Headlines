from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


class CategoryRule:
    def __init__(self, slug: str, keywords: list[str], entities: list[str]):
        self.slug = slug
        self.keywords = keywords
        self.entities = entities


class CategoryRegistry:
    def __init__(self, rules: dict[str, CategoryRule], version: int):
        self.rules = rules
        self.version = version


def load_category_registry() -> CategoryRegistry:
    base_path = Path(__file__).parent.parent / "taxonomy" / "rules.yml"

    with open(base_path, "r") as f:
        data = yaml.safe_load(f) or {}

    version = int(data.get("version", 1))
    raw_rules: dict[str, Any] = data.get("rules", {})

    rules: dict[str, CategoryRule] = {}

    for slug, rule in raw_rules.items():
        if slug in rules:
            raise ValueError(f"Duplicate category rule slug: {slug}")

        keywords = [str(x).strip().lower() for x in rule.get("keywords", []) if str(x).strip()]
        entities = [str(x).strip() for x in rule.get("entities", []) if str(x).strip()]

        rules[slug] = CategoryRule(
            slug=slug,
            keywords=keywords,
            entities=entities,
        )

    return CategoryRegistry(rules=rules, version=version)


def _flatten_categories(node: dict | None, prefix: str = "") -> list[str]:
    """Recursively extract all leaf slug paths from the categories.yml hierarchy."""
    if node is None:
        return [prefix] if prefix else []
    slugs: list[str] = []
    for key, children in (node or {}).items():
        slug = f"{prefix}.{key}" if prefix else key
        if children is None:
            slugs.append(slug)
        else:
            slugs.extend(_flatten_categories(children, slug))
    return slugs


# ── Display-name helpers ──────────────────────────────────────────────────────

_FULL_SLUG_LABELS: dict[str, str] = {
    "politics.uk":     "UK Politics",
    "politics.us":     "US Politics",
    "politics.europe": "European Politics",
    "politics.global": "World Politics",
}

_PART_LABELS: dict[str, str] = {
    "ai":               "AI",
    "tv-film":          "TV & Film",
    "formula1":         "Formula 1",
    "premier-league":   "Premier League",
    "championship":     "Championship",
    "champions-league": "Champions League",
    "rugby-union":      "Rugby Union",
    "rugby-league":     "Rugby League",
    "middle-east":      "Middle East",
    "personal-finance": "Personal Finance",
    "nfl":              "NFL",
    "nba":              "NBA",
    "mma":              "MMA",
    "pga":              "PGA",
    "liv":              "LIV",
    "uk":               "UK",
    "us":               "US",
}


def _item_label(full_slug: str, part: str) -> str:
    if full_slug in _FULL_SLUG_LABELS:
        return _FULL_SLUG_LABELS[full_slug]
    return _PART_LABELS.get(part, part.replace("-", " ").title())


def _build_group(full_slug: str, part: str, children: dict | None) -> dict:
    """One taxonomy node → UI dict, recursing to arbitrary depth. `subcategories`
    is the (possibly empty) list of child nodes; a leaf has `subcategories == []`.
    Slugs are the full dotted path (e.g. sport.football.premier-league.arsenal) so
    they remain valid backend filter slugs at every level."""
    subs: list[dict] = []
    for child_part, grandchildren in (children or {}).items():
        subs.append(_build_group(f"{full_slug}.{child_part}", child_part, grandchildren))
    return {
        "slug":          full_slug,
        "label":         _item_label(full_slug, part),
        "subcategories": subs,
    }


@lru_cache(maxsize=1)
def load_category_groups() -> list[dict]:
    """Return the FULL category hierarchy for UI display — arbitrary depth
    (e.g. sport → football → premier-league → arsenal), not collapsed. The picker
    renders every level; leaf nodes carry an empty `subcategories` list. The full
    fixed taxonomy is always returned regardless of current story coverage.
    """
    cats_path = Path(__file__).parent.parent / "taxonomy" / "categories.yml"
    with open(cats_path, "r") as f:
        data = yaml.safe_load(f) or {}
    raw: dict[str, Any] = data.get("categories", {}) or {}

    return [_build_group(top_slug, top_slug, children) for top_slug, children in raw.items()]


@lru_cache(maxsize=1)
def load_valid_category_slugs() -> frozenset[str]:
    """
    Return all valid category slugs from categories.yml.

    Includes leaf nodes like 'science', 'climate', 'health' that have no
    keyword rules in rules.yml but are assignable via entity boosts.
    Used as the permitted-slug list for the LLM fallback classifier.
    """
    cats_path = Path(__file__).parent.parent / "taxonomy" / "categories.yml"
    with open(cats_path, "r") as f:
        data = yaml.safe_load(f) or {}
    raw: dict[str, Any] = data.get("categories", {})
    return frozenset(_flatten_categories(raw))