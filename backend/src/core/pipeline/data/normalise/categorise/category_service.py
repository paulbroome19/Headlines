from __future__ import annotations

from dataclasses import dataclass

from .category_loader import load_category_registry
from .category_matcher import match_categories
from .entity_loader import load_entity_registry


@dataclass(frozen=True)
class CategoryCategorisationResult:
    category_slugs: list[str]
    primary_category: str | None
    scores: dict[str, float]
    method: str
    version: int


_category_registry = None
_entity_registry = None


def _get_category_registry():
    global _category_registry
    if _category_registry is None:
        _category_registry = load_category_registry()
    return _category_registry


def _get_entity_registry():
    global _entity_registry
    if _entity_registry is None:
        _entity_registry = load_entity_registry()
    return _entity_registry


def categorise_categories(
    *,
    title: str,
    content_snippet: str | None,
    entity_slugs: list[str],
) -> CategoryCategorisationResult:
    category_registry = _get_category_registry()
    entity_registry = _get_entity_registry()

    ranked, primary = match_categories(
        title=title,
        content_snippet=content_snippet,
        entity_slugs=entity_slugs,
        registry=category_registry,
        entity_registry=entity_registry,
    )

    if not ranked:
        return CategoryCategorisationResult(
            category_slugs=[],
            primary_category=None,
            scores={},
            method="unclassified",
            version=category_registry.version,
        )

    scores = {r.slug: r.score for r in ranked}
    slugs = [r.slug for r in ranked]

    return CategoryCategorisationResult(
        category_slugs=slugs,
        primary_category=primary,
        scores=scores,
        method="hybrid",
        version=category_registry.version,
    )