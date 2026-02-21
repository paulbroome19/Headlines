from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session


# -----------------------------
# Load entity registry once
# -----------------------------

_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "taxonomy" / "entities.yaml"
)

if _REGISTRY_PATH.exists():
    with open(_REGISTRY_PATH, "r") as f:
        _ENTITY_REGISTRY = yaml.safe_load(f) or {}
else:
    _ENTITY_REGISTRY = {}

_ENTITY_REGISTRY = _ENTITY_REGISTRY.get("entities", {})


class NormalisationArticleRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    # -----------------------------
    # Entity Registry Metadata
    # -----------------------------

    def get_registry_metadata(self, slug: str) -> Dict[str, Any]:
        """
        Fetch entity metadata from YAML registry.
        Raises if slug not defined.
        """
        if slug not in _ENTITY_REGISTRY:
            raise ValueError(f"Entity slug '{slug}' not found in registry")

        return {
            "display_name": _ENTITY_REGISTRY[slug]["display_name"],
            "entity_type": _ENTITY_REGISTRY[slug]["entity_type"],
        }

    # -----------------------------
    # Entity DB Operations
    # -----------------------------

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

        return int(row) if row else None

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