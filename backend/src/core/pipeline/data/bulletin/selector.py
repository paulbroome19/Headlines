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


def selection_token(ranking_run_id: int, request_hash: str) -> str:
    """The SELECTION identity: WHICH materialised selection an artifact belongs to = the pinned
    ranking run + the request_hash. Threaded through edition → bulletin → manifest → readiness →
    (eventually) events. Two artifacts with different tokens came from DIFFERENT selections, so
    serving one against the other is a cross-selection serve (board≠audio) — that must fail LOUDLY,
    never be silently served. Stable across a heard/skip reconcile (which changes story_ids but not
    the run/hash), so it flags only genuine run drift / cross-selection, not normal edition churn."""
    return f"{ranking_run_id}:{request_hash}"


def parse_selection_run(selection_id: str | None) -> int | None:
    """The run component of a `selection_id` token (`"{run}:{hash}"`), or None if absent/malformed.
    L-D: the client sends the tapped selection_id on the manifest POST; the server probes the cache
    with THIS run so it serves exactly the selection the board committed to — a newer run landing
    between render and tap can't swap it. None → server behaves as today (backward compatible)."""
    pin = parse_selection_token(selection_id)
    return pin[0] if pin is not None else None


def parse_selection_token(selection_id: str | None) -> tuple[int, str] | None:
    """The FULL (run, hash) identity of a `selection_id` token (`"{run}:{hash}"`), or None if
    absent/malformed.

    L-D completed: the client sends the tapped selection_id on the manifest POST and the server
    serves EXACTLY that materialised edition — the run pins the ranking, and the HASH pins the
    edition's membership + order. Parsing the run ALONE (the old `parse_selection_run`) was the
    L-D hole: the manifest recomputed the request_hash from the live profile, so any hash drift
    between render and tap (a filter change, or the UK day rolling at 23:00 UTC — the hash folds in
    the day) served a DIFFERENT edition under the client's pin → home order ≠ briefing order. Return
    both so the manifest never recomputes the hash over a pinned selection. None → the server behaves
    as today (backward compatible: no pin → recompute from the profile)."""
    if not selection_id or ":" not in selection_id:
        return None
    run_part, hash_part = selection_id.split(":", 1)
    if not hash_part:
        return None
    try:
        return int(run_part), hash_part
    except ValueError:
        return None


def resolve_selection_pin(
    client_pin: tuple[int, str] | None,
    current_hash: str,
    *,
    pinned_edition_exists: bool,
) -> tuple[str, bool, bool]:
    """Pure L-D decision (no IO): given the client's tapped (run, hash) pin and today's recomputed
    request_hash, decide WHICH edition hash to serve and whether this is a deliberate refresh.

    Returns (request_hash, honor_pin, selection_refreshed):
      - honor_pin True  → the pinned edition exists → serve EXACTLY the pinned hash (the client's run
        also wins the run probe). Home order == briefing order, guaranteed.
      - honor_pin False → serve the current hash. `selection_refreshed` is True iff the client sent a
        pin we could NOT honor whose hash differs from current — i.e. the pinned edition is gone (the
        UK day rolled at 23:00 UTC, or filters changed and no edition exists for that hash). That is a
        DELIBERATE refresh to the current edition, flagged so the client re-syncs to a fresh preview
        state — never a silent serve of a different order under the client's stale pin.
      - No pin, or a pin whose hash already equals current → honor_pin False, selection_refreshed
        False → unchanged behaviour (recompute from the profile; cold play-first / warmer path)."""
    if client_pin is not None and pinned_edition_exists:
        return client_pin[1], True, False
    stale = client_pin is not None and client_pin[1] != current_hash
    return current_hash, False, stale


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
