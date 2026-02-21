from typing import Dict, List

from .entity_loader import load_entity_registry
from .entity_matcher import match_entities, EntityMatchResult


class EntityCategorisationResult:
    def __init__(
        self,
        entity_slugs: List[str],
        primary_entity: str | None,
        scores: Dict[str, float],
        method: str = "keywords",
        version: int = 1,
    ):
        self.entity_slugs = entity_slugs
        self.primary_entity = primary_entity
        self.scores = scores
        self.method = method
        self.version = version


_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        _registry = load_entity_registry()
    return _registry


def categorise_article(
    *,
    title: str,
    content_snippet: str | None,
) -> EntityCategorisationResult:

    registry = _get_registry()

    ranked, primary = match_entities(
        title=title,
        content_snippet=content_snippet,
        registry=registry,
    )

    if not ranked:
        return EntityCategorisationResult(
            entity_slugs=[],
            primary_entity=None,
            scores={},
        )

    scores = {r.slug: r.score for r in ranked}
    slugs = [r.slug for r in ranked]

    return EntityCategorisationResult(
        entity_slugs=slugs,
        primary_entity=primary,
        scores=scores,
    )