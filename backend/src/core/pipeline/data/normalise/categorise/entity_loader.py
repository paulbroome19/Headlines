import yaml
from pathlib import Path
from typing import Dict, List


class EntityRegistry:
    def __init__(self, slug_map: Dict[str, dict], alias_map: Dict[str, str]):
        self.slug_map = slug_map          # slug -> metadata
        self.alias_map = alias_map        # alias (lower) -> slug


def load_entity_registry() -> EntityRegistry:
    base_path = Path(__file__).parent.parent / "taxonomy" / "entities.yml"

    with open(base_path, "r") as f:
        data = yaml.safe_load(f)

    entities = data.get("entities", {})
    slug_map: Dict[str, dict] = {}
    alias_map: Dict[str, str] = {}

    for slug, meta in entities.items():
        if slug in slug_map:
            raise ValueError(f"Duplicate entity slug detected: {slug}")

        slug_map[slug] = meta

        aliases: List[str] = meta.get("aliases", [])
        for alias in aliases:
            normalized = alias.strip().lower()
            if normalized in alias_map:
                raise ValueError(
                    f"Alias collision detected: '{normalized}' "
                    f"between {alias_map[normalized]} and {slug}"
                )
            alias_map[normalized] = slug

    return EntityRegistry(slug_map=slug_map, alias_map=alias_map)