from __future__ import annotations

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