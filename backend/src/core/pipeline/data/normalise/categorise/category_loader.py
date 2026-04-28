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


@lru_cache(maxsize=1)
def load_category_groups() -> list[dict]:
    """Return two-level category hierarchy for UI display.

    Top-level keys become group headers; their direct children become
    selectable items.  Three-level nesting (e.g. sport.football.premier-league)
    is collapsed — only the second level is exposed.
    """
    cats_path = Path(__file__).parent.parent / "taxonomy" / "categories.yml"
    with open(cats_path, "r") as f:
        data = yaml.safe_load(f) or {}
    raw: dict[str, Any] = data.get("categories", {}) or {}

    groups: list[dict] = []
    for top_slug, children in raw.items():
        items: list[dict] = []
        if children:
            for child_slug in children:
                full = f"{top_slug}.{child_slug}"
                items.append({"slug": full, "label": _item_label(full, child_slug)})
        groups.append({
            "slug":           top_slug,
            "label":          _item_label(top_slug, top_slug),
            "subcategories":  items,
        })
    return groups


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