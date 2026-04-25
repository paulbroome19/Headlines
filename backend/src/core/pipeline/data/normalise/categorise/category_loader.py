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