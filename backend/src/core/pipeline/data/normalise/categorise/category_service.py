from __future__ import annotations

from dataclasses import dataclass, field

from core.platform.config.settings import settings
from .category_loader import load_category_registry, load_valid_category_slugs
from .category_matcher import match_categories
from .entity_loader import load_entity_registry
from .llm_fallback import classify_fallback, FALLBACK_CONFIDENCE_THRESHOLD


@dataclass(frozen=True)
class CategoryCategorisationResult:
    category_slugs: list[str]
    primary_category: str | None
    scores: dict[str, float]
    method: str
    version: int
    # Populated only when method == "llm-fallback"; not persisted to DB but
    # available in-process for logging and devtool inspection.
    fallback_confidence: float | None = field(default=None, compare=False)
    fallback_reason: str | None = field(default=None, compare=False)


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
        if not settings.enable_llm_categorise_fallback:
            return CategoryCategorisationResult(
                category_slugs=[],
                primary_category=None,
                scores={},
                method="unclassified",
                version=category_registry.version,
            )
        valid_slugs = list(load_valid_category_slugs())
        fb = classify_fallback(title, content_snippet, valid_slugs)
        if fb is not None and fb.accepted:
            return CategoryCategorisationResult(
                category_slugs=[fb.slug],
                primary_category=fb.slug,
                scores={fb.slug: fb.confidence},
                method="llm-fallback",
                version=category_registry.version,
                fallback_confidence=fb.confidence,
                fallback_reason=fb.reason,
            )
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