import re
from typing import List, Tuple

from .entity_loader import EntityRegistry


class EntityMatchResult:
    def __init__(self, slug: str, score: float):
        self.slug = slug
        self.score = score


def _normalize_text(text: str) -> str:
    # Lowercase + collapse whitespace
    return re.sub(r"\s+", " ", text.lower())


def _alias_pattern(alias: str) -> re.Pattern:
    """
    Create word-boundary safe regex pattern.
    Prevents partial matches.
    """
    escaped = re.escape(alias)
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


def match_entities(
    *,
    title: str,
    content_snippet: str | None,
    registry: EntityRegistry,
) -> Tuple[List[EntityMatchResult], str | None]:
    """
    Returns:
        - List of EntityMatchResult (sorted by score desc)
        - Primary entity slug (or None)
    """

    combined = f"{title} {content_snippet or ''}"
    normalized_text = _normalize_text(combined)

    scores = {}

    for alias, slug in registry.alias_map.items():
        pattern = _alias_pattern(alias)
        matches = pattern.findall(normalized_text)

        if matches:
            # Weight title matches slightly higher
            title_matches = pattern.findall(title.lower())

            score = len(matches) + (len(title_matches) * 1.5)

            scores[slug] = scores.get(slug, 0) + score

    if not scores:
        return [], None

    ranked = sorted(
        [EntityMatchResult(slug=s, score=score) for s, score in scores.items()],
        key=lambda x: x.score,
        reverse=True,
    )

    primary = ranked[0].slug if ranked else None

    return ranked, primary