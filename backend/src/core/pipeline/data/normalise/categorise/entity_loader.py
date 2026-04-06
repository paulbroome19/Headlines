from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class EntityMetadata:
    slug: str
    display_name: str
    entity_type: str
    aliases: list[str]
    categories: list[str]


class EntityRegistry:
    def __init__(self, slug_map: dict[str, EntityMetadata], alias_map: dict[str, str], version: int):
        self.slug_map = slug_map          # slug -> EntityMetadata
        self.alias_map = alias_map        # alias (lower) -> slug
        self.version = version


def load_entity_registry() -> EntityRegistry:
    base_path = Path(__file__).parent.parent / "taxonomy" / "entities.yml"

    with open(base_path, "r") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    version = int(data.get("version", 1))
    raw_entities: dict[str, Any] = data.get("entities", {})

    slug_map: dict[str, EntityMetadata] = {}
    alias_map: dict[str, str] = {}

    for slug, meta in raw_entities.items():
        if slug in slug_map:
            raise ValueError(f"Duplicate entity slug detected: {slug}")

        display_name = str(meta.get("display_name", "")).strip()
        entity_type = str(meta.get("entity_type", "")).strip()
        aliases = [str(x).strip() for x in meta.get("aliases", []) if str(x).strip()]
        categories = [str(x).strip() for x in meta.get("categories", []) if str(x).strip()]

        if not display_name:
            raise ValueError(f"Entity '{slug}' missing display_name")
        if not entity_type:
            raise ValueError(f"Entity '{slug}' missing entity_type")

        entity = EntityMetadata(
            slug=slug,
            display_name=display_name,
            entity_type=entity_type,
            aliases=aliases,
            categories=categories,
        )
        slug_map[slug] = entity

        for alias in aliases:
            normalized = alias.lower()
            if normalized in alias_map:
                raise ValueError(
                    f"Alias collision detected: '{normalized}' "
                    f"between {alias_map[normalized]} and {slug}"
                )
            alias_map[normalized] = slug

    return EntityRegistry(slug_map=slug_map, alias_map=alias_map, version=version)