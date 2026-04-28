"""
Story selection and request hashing for user-specific bulletin assembly.

Pure functions — no IO, no LLM.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_request_hash(filters: dict[str, Any]) -> str:
    """
    16-char hex SHA-256 of the canonicalised filter dict.
    Used as the cache key in UNIQUE(ranking_run_id, request_hash).
    """
    canonical = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def validate_filter_categories(cats: list[str]) -> list[str]:
    """
    Return error messages for categories that are neither a valid leaf slug
    nor a known parent prefix in the taxonomy.
    Accepts: exact leaf slug OR parent prefix of at least one leaf slug.
    """
    from core.pipeline.data.normalise.categorise.category_loader import load_valid_category_slugs

    valid_slugs = load_valid_category_slugs()
    errors: list[str] = []
    for cat in cats:
        if not cat or not isinstance(cat, str):
            errors.append(f"invalid category value: {cat!r}")
            continue
        if cat in valid_slugs or any(s.startswith(cat + ".") for s in valid_slugs):
            continue
        errors.append(f"unknown category: {cat!r}")
    return errors


def select_stories(
    summaries: list[dict[str, Any]],
    *,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    max_stories: int = 20,
) -> list[dict[str, Any]]:
    """
    Filter and cap a ranked story list.

    Rules:
    - Preserves existing ranking order (no re-ranking)
    - include_categories: keep only stories matching at least one pattern
    - exclude_categories: drop stories matching any pattern
    - max_stories: hard limit; returns fewer if fewer match (no backfill)
    - Empty/None include_categories → no include filter (keep all)
    - Empty/None exclude_categories → no exclude filter

    Category matching is hierarchical:
      "politics"    matches "politics.uk", "politics.general", ...
      "politics.uk" matches only "politics.uk" (and theoretical children)
    """
    result: list[dict[str, Any]] = []

    for story in summaries:
        cat = (story.get("primary_category") or "").strip()

        if include_categories and not _matches_any(cat, include_categories):
            continue

        if exclude_categories and _matches_any(cat, exclude_categories):
            continue

        result.append(story)
        if len(result) >= max_stories:
            break

    return result


_WORDS_PER_MINUTE = 150


def estimate_duration_seconds(audio_script: str) -> float:
    return len(audio_script.split()) / _WORDS_PER_MINUTE * 60


def select_stories_by_duration(
    summaries: list[dict[str, Any]],
    *,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    max_duration_minutes: float = 5.0,
) -> list[dict[str, Any]]:
    """
    Select stories fitting within a duration budget (estimated at 150 wpm).

    Always includes at least one story.  Stops before adding a story that would
    exceed the budget unless the result list is still empty.
    """
    budget = max_duration_minutes * 60
    result: list[dict[str, Any]] = []
    used = 0.0

    for story in summaries:
        cat = (story.get("primary_category") or "").strip()

        if include_categories and not _matches_any(cat, include_categories):
            continue
        if exclude_categories and _matches_any(cat, exclude_categories):
            continue

        script = (story.get("audio_script") or "").strip() or (story.get("summary_text") or "")
        duration = estimate_duration_seconds(script)
        if used + duration > budget and result:
            break

        result.append(story)
        used += duration

    return result


def _matches_any(category: str, patterns: list[str]) -> bool:
    return any(category == p or category.startswith(p + ".") for p in patterns)
