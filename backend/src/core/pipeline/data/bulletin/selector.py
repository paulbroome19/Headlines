"""
Story selection and request hashing for user-specific bulletin assembly.

Pure functions — no IO, no LLM.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

# Bump to retroactively invalidate every cached bulletin AND materialised edition — do this
# whenever the selection ORDER or MEMBERSHIP logic changes (e.g. the #169/#170 region-grouped
# top block), so a code change can never serve an old-order cached bulletin against a freshly
# re-materialised edition. The version is folded into request_hash, so old rows simply stop
# matching and are rebuilt on next request. (Was implicitly 1 pre-#170; 2 = post-#170 region order.)
SELECTION_VERSION = 2


def selection_filters(
    *,
    preset: str,
    include_top_stories: bool,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """The canonical selection-filter dict for a PROFILE-based briefing — the single source of
    truth for the (profile, day) request_hash.

    Home preview, the streaming skeleton, the cached/assemble path and the background assembly
    ALL build it here so their request_hash can never drift. This closes the #157 wiring bug:
    the manifest used to key `include_top_stories` off the client request (iOS hardcodes True)
    while the preview keyed it off the profile, so a profile with top-stories OFF produced two
    different hashes — the skeleton then missed the preview's materialised (deduped) selection
    and fell back to the pre-dedup order (and a different set of stories) even in the warm path.

    Keep the shape identical to every caller's cache key: `preset` + `include_top_stories` are
    always present; categories (sorted) and name are included only when set.
    """
    filters: dict[str, Any] = {"preset": preset, "include_top_stories": include_top_stories}
    if include_categories:
        filters["include_categories"] = sorted(include_categories)
    if exclude_categories:
        filters["exclude_categories"] = sorted(exclude_categories)
    if name:
        filters["name"] = name.strip()
    return filters


def compute_request_hash(
    filters: dict[str, Any],
    *,
    day: date,
    selection_version: int = SELECTION_VERSION,
) -> str:
    """
    16-char hex SHA-256 of the canonicalised (filters + day + selection_version) tuple.
    Used as the cache key in UNIQUE(ranking_run_id, request_hash).

    `day` and `selection_version` are folded in so the BULLETIN cache key gains the same day
    dimension the edition key already has (its `edition_date` column) — closing the asymmetry
    where the edition re-materialised per day but the bulletin cache (ranking_run_id, request_hash)
    was reused across days, so a selection-code change could serve an old-order cached bulletin
    against a new-order edition. Both surfaces compute the hash here with the SAME day, so they
    can never drift (the #157 parity invariant holds). Output stays 16-char hex, so callers that
    use `int(request_hash, 16)` as the assembler RNG seed keep working.
    """
    payload = {"f": filters, "d": day.isoformat(), "v": selection_version}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
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
