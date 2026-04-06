from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.pipeline.data.normalise.categorise.entity_loader import load_entity_registry


_ENTITY_REGISTRY = None


def _get_entity_registry():
    global _ENTITY_REGISTRY
    if _ENTITY_REGISTRY is None:
        _ENTITY_REGISTRY = load_entity_registry()
    return _ENTITY_REGISTRY


class NormalisationArticleRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --------------------------------------------------
    # Entity registry metadata
    # --------------------------------------------------

    def get_registry_metadata(self, slug: str) -> dict[str, Any]:
        registry = _get_entity_registry()
        entity = registry.slug_map.get(slug)

        if entity is None:
            raise ValueError(f"Entity slug '{slug}' not found in registry")

        return {
            "display_name": entity.display_name,
            "entity_type": entity.entity_type,
            "categories": entity.categories,
        }

    # --------------------------------------------------
    # Entities
    # --------------------------------------------------

    def get_entity_id_by_slug(self, slug: str) -> int | None:
        row = self.db.execute(
            text(
                """
                SELECT id
                FROM data.entities
                WHERE slug = :slug
                LIMIT 1
                """
            ),
            {"slug": slug},
        ).scalar_one_or_none()

        return int(row) if row is not None else None

    def insert_entity(self, slug: str, display_name: str, entity_type: str) -> int:
        new_id = self.db.execute(
            text(
                """
                INSERT INTO data.entities (
                    slug,
                    display_name,
                    entity_type
                )
                VALUES (
                    :slug,
                    :display_name,
                    :entity_type
                )
                RETURNING id
                """
            ),
            {
                "slug": slug,
                "display_name": display_name,
                "entity_type": entity_type,
            },
        ).scalar_one()

        return int(new_id)

    def link_entity_to_article(
        self,
        *,
        normalisation_article_id: int,
        entity_id: int,
        is_primary: bool,
        confidence_score: float,
    ) -> None:
        self.db.execute(
            text(
                """
                INSERT INTO data.normalisation_article_entities (
                    normalisation_article_id,
                    entity_id,
                    is_primary,
                    confidence_score
                )
                VALUES (
                    :normalisation_article_id,
                    :entity_id,
                    :is_primary,
                    :confidence_score
                )
                """
            ),
            {
                "normalisation_article_id": normalisation_article_id,
                "entity_id": entity_id,
                "is_primary": is_primary,
                "confidence_score": confidence_score,
            },
        )