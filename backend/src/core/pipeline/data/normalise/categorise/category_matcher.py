from __future__ import annotations

import re
from dataclasses import dataclass

from .category_loader import CategoryRegistry
from .entity_loader import EntityRegistry


@dataclass(frozen=True)
class CategoryMatchResult:
    slug: str
    score: float


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword)
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


def match_categories(
    *,
    title: str,
    content_snippet: str | None,
    entity_slugs: list[str],
    registry: CategoryRegistry,
    entity_registry: EntityRegistry,
) -> tuple[list[CategoryMatchResult], str | None]:
    combined = f"{title or ''} {content_snippet or ''}".strip()
    normalized_text = _normalize_text(combined)
    normalized_title = _normalize_text(title or "")

    scores: dict[str, float] = {}

    for slug, rule in registry.rules.items():
        score = 0.0

        # --------------------------------------------------
        # 1) Keyword scoring
        # --------------------------------------------------
        for keyword in rule.keywords:
            pattern = _keyword_pattern(keyword)
            full_matches = len(pattern.findall(normalized_text))
            title_matches = len(pattern.findall(normalized_title))

            if full_matches:
                score += float(full_matches)

            if title_matches:
                score += float(title_matches) * 1.5

        # --------------------------------------------------
        # 2) Explicit rule-based entity boosts
        # --------------------------------------------------
        for entity_slug in rule.entities:
            if entity_slug in entity_slugs:
                score += 3.0

        if score > 0:
            scores[slug] = score

    # --------------------------------------------------
    # 3) Entity -> category boosts from entities.yml
    # --------------------------------------------------
    # Applied OUTSIDE the rule loop so entity-only categories are reachable — a
    # football club's team category (e.g. sport.football.premier-league.man-city)
    # has no keyword rule and is populated purely by ENTITY matching. Iterating
    # only rule slugs would leave those categories unscored.
    for entity_slug in entity_slugs:
        entity_meta = entity_registry.slug_map.get(entity_slug)
        if not entity_meta:
            continue
        # Countries are mentioned in many unrelated articles; a lower boost
        # prevents a country name in a snippet from overriding the primary topic
        # signal (e.g. "I'm A Celebrity filmed in South Africa").
        boost = 1.5 if entity_meta.entity_type == "country" else 5.0
        for cat in entity_meta.categories:
            scores[cat] = scores.get(cat, 0.0) + boost

    if not scores:
        return [], None

    ranked = sorted(
        [CategoryMatchResult(slug=s, score=sc) for s, sc in scores.items()],
        key=lambda x: (x.score, len(x.slug)),
        reverse=True,
    )

    primary = ranked[0].slug if ranked else None
    return ranked, primary