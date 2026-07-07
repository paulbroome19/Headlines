"""
ONE selection, two renderings (audit §3).

Home preview and the briefing must not be two parallel selection paths we keep patching
into agreement — they must render the SAME materialised selection. `build_selection` is the
single canonical pipeline; `resolve_materialised_selection` runs it once per (profile, day,
request_hash), pins it to a ranking run, and stores the result so both the preview and the
final briefing read identical ordered story ids.

Scope: the `read_from_ranked_list=true` path (prod). The legacy live-scoring path is left on
its existing code behind the flag.

Order (per the task): qualify_and_order → dropped-filter → dedup_same_event → cap_bulletin.
dedup runs BEFORE cap (so cap fills N with distinct events) and operates on the candidates'
representative titles, so it needs no summaries. `dedup_same_event` keeps the best-ranked
(lowest-index) member of each same-event group, so the LEAD (index 0) can never be dropped —
the opener gate always synthesises the correct first story.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.platform.config.settings import settings
from core.pipeline.data.bulletin.connective import uk_now
from core.pipeline.data.bulletin.edition import UserDailyEditionRepo, _categories_for
from core.pipeline.data.bulletin.event_dedup import dedup_same_event
from core.pipeline.data.bulletin.repos.bulletin_repo import BulletinRepo
from core.pipeline.data.bulletin.repos.user_story_state_repo import UserStoryStateRepo
from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.depth import depth_for_rank
from core.pipeline.ranking.ranked_list import scored_from_ranked_list
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo
from core.pipeline.ranking.thresholds import (
    DEFAULT_PRESET,
    PRESET_BAR,
    _fresh_category_relief,
    _matches_category,
    _selection_context,
    cap_bulletin,
    qualify_and_order,
)

logger = logging.getLogger(__name__)

_ensured = False


def _ensure_columns(db: Session) -> None:
    """Idempotent: pin the materialised selection to a ranking run (mirrors editorial_review's
    runtime ADD COLUMN IF NOT EXISTS pattern — the alembic graph has multiple heads)."""
    global _ensured
    if _ensured:
        return
    db.execute(text("ALTER TABLE data.user_daily_editions ADD COLUMN IF NOT EXISTS ranking_run_id BIGINT"))
    db.commit()
    _ensured = True


def _excluder(exclude_categories: list[str] | None):
    excludes = list(exclude_categories or [])

    def _excluded(cat: str | None) -> bool:
        return bool(cat) and any(cat == e or cat.startswith(e + ".") for e in excludes)

    return _excluded


def _sid_key(story_id: str):
    """Sort key for 'lower story_id' — numeric ids compared numerically, others lexically."""
    return (0, int(story_id)) if str(story_id).isdigit() else (1, str(story_id))


def guard_top_block(
    reservoir: list,
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    preset: str,
    now: datetime | None = None,
) -> list:
    """Deterministic backstop for the LLM fragment-dedup (#156 rider 2b): if two stories in the
    TOP-STORIES block share (primary entity, top-level category), keep the better one and demote
    the rest OUT of the top block. A demoted story stays only if it independently clears the topic
    bar as a normal followed-topic story; otherwise it's dropped (a below-bar / unfollowed-category
    story can't legitimately be a topic story, and must not fall back into the top block).

    Runs AFTER dedup_same_event (backstop for what the LLM misses, not a replacement) and BEFORE
    cap_bulletin, so preview/blocking/streaming inherit it identically. Top-block-ONLY: followed-
    topic buckets are untouched (two same-entity stories in a topic feed are legitimate). No-entity
    (hint-path) stories are exempt — never grouped. Deterministic; no LLM. The lead (index 0) is
    always its group's kept member, so the guard can never change the lead."""
    if len(reservoir) < 2:
        return reservoir

    front_page, regions, topic_cats = _selection_context(include_top_stories, include_categories)
    ref_now = now or datetime.now(timezone.utc)
    bar = PRESET_BAR.get(preset) or PRESET_BAR[DEFAULT_PRESET]
    lead_id = reservoir[0].candidate.story_id

    def _in_top(s) -> bool:
        return bool(s.candidate.top_story) and (front_page or (s.candidate.geo_region in regions))

    def _group_key(s):
        ent = s.candidate.primary_entity_id
        if not ent:
            return None  # no primary entity → exempt, never grouped
        return (ent, (s.candidate.primary_category or "").split(".")[0])

    def _clears_topic_bar(s) -> bool:
        cat = s.candidate.primary_category
        return (s.normalized_score >= (bar - _fresh_category_relief(s, ref_now))
                and any(_matches_category(cat, tc) for tc in topic_cats))

    groups: dict = {}
    for s in reservoir:
        if not _in_top(s):
            continue
        k = _group_key(s)
        if k is None:
            continue
        groups.setdefault(k, []).append(s)

    remove: set[str] = set()
    for k, members in groups.items():
        if len(members) < 2:
            continue
        # Winner: the lead if it's in this group (invariant — index 0 is never demoted); else the
        # higher-merit story (tiebreak lower story_id).
        winner = next((m for m in members if m.candidate.story_id == lead_id), None)
        if winner is None:
            winner = min(members, key=lambda s: (-s.normalized_score, _sid_key(s.candidate.story_id)))
        for loser in members:
            if loser is winner:
                continue
            loser.candidate.top_story = False   # demote out of the top block
            kept = _clears_topic_bar(loser)
            if not kept:
                remove.add(loser.candidate.story_id)
            logger.info(
                "top_block_guard: demoted story=%s (shared entity+topcat=%s with kept=%s) "
                "kept_as_followed_topic=%s", loser.candidate.story_id, list(k),
                winner.candidate.story_id, kept,
            )

    if not remove:
        return reservoir
    return [s for s in reservoir if s.candidate.story_id not in remove]


def finalize_selection(
    scored: list,
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    exclude_categories: list[str] | None,
    preset: str,
    dropped: set[str],
    dedup_fn=dedup_same_event,
    now: datetime | None = None,
) -> list[dict]:
    """PURE canonical pipeline over an already-scored reservoir: qualify_and_order → drop
    excluded/heard/skipped → dedup_same_event (BEFORE cap, on representative titles) →
    cap_bulletin. Returns ordered [{story_id, primary_category}]. `dedup_fn` is injectable for
    tests. dedup keeps the best-ranked member of each same-event group, so the LEAD survives."""
    reservoir = qualify_and_order(
        scored, include_top_stories=include_top_stories,
        include_categories=include_categories, preset=preset,
    )
    _excluded = _excluder(exclude_categories)
    reservoir = [
        s for s in reservoir
        if not _excluded(s.candidate.primary_category) and s.candidate.story_id not in dropped
    ]
    if dedup_fn is not None:
        stub = [{"story_id": s.candidate.story_id, "headline": s.candidate.title or ""} for s in reservoir]
        kept = {d["story_id"] for d in dedup_fn(stub)}
        reservoir = [s for s in reservoir if s.candidate.story_id in kept]

    # Deterministic top-block dedup: after the LLM same-event dedup (its backstop), before cap.
    reservoir = guard_top_block(
        reservoir, include_top_stories=include_top_stories,
        include_categories=include_categories, preset=preset, now=now,
    )

    display = cap_bulletin(
        reservoir, include_top_stories=include_top_stories,
        include_categories=include_categories, preset=preset,
    )
    return [{"story_id": s.candidate.story_id, "primary_category": s.candidate.primary_category} for s in display]


def build_selection(
    db: Session,
    *,
    profile_id: int | None,
    include_top_stories: bool,
    include_categories: list[str] | None,
    exclude_categories: list[str] | None,
    preset: str,
    dedup: bool = True,
) -> list[dict]:
    """The canonical ordered selection (read_from_ranked_list path): load the stable-list
    reservoir, then finalize_selection. `dedup=False` gives the fast pre-dedup order for the
    cold-start streaming skeleton (never on the first-play LLM path)."""
    candidates = load_story_ranking_candidates(db)
    scored = scored_from_ranked_list(db, candidates)
    dropped = (
        {str(x) for x in UserStoryStateRepo(db).get_dropped_story_ids(profile_id)}
        if profile_id is not None else set()
    )
    return finalize_selection(
        scored, include_top_stories=include_top_stories, include_categories=include_categories,
        exclude_categories=exclude_categories, preset=preset, dropped=dropped,
        dedup_fn=dedup_same_event if dedup else None,
    )


def _depths_for(ordered: list[dict]) -> dict[str, tuple[str, int]]:
    return {o["story_id"]: depth_for_rank(i) for i, o in enumerate(ordered)}


def _ordered_from_ids(db: Session, ids: list[str]) -> list[dict]:
    cats = _categories_for(db, ids)
    return [{"story_id": sid, "primary_category": cats.get(sid)} for sid in ids]


def get_materialised_selection(
    db: Session, *, profile_id: int, request_hash: str, day: date | None = None
) -> tuple[list[dict], dict, int] | None:
    """READ-ONLY: the stored materialised (deduped) selection for today, or None if not built
    yet. Used by the streaming skeleton so it matches the final briefing when the preview has
    already materialised it — without ever running the LLM on the first-play path."""
    _ensure_columns(db)
    day = day or uk_now().date()
    row = db.execute(
        text("""SELECT story_ids, ranking_run_id FROM data.user_daily_editions
                WHERE profile_id = :p AND edition_date = :d AND request_hash = :h
                  AND ranking_run_id IS NOT NULL"""),
        {"p": profile_id, "d": day, "h": request_hash},
    ).first()
    if row is None:
        return None
    ids = row[0] if isinstance(row[0], list) else __import__("json").loads(row[0])
    ids = [str(x) for x in ids]
    ordered = _ordered_from_ids(db, ids)
    return ordered, _depths_for(ordered), int(row[1])


def resolve_materialised_selection(
    db: Session,
    *,
    profile_id: int | None,
    request_hash: str,
    include_top_stories: bool,
    include_categories: list[str] | None,
    exclude_categories: list[str] | None,
    preset: str,
) -> tuple[list[dict], dict, int]:
    """The single source of truth for the preview AND the final briefing. Resolves the pinned
    ranking run (freshness rule: pin for the day; refresh to a newer run only if the user hasn't
    started that briefing yet — no bulletin built for the pinned run), builds+stores the deduped
    selection on first access, and returns (ordered, depths, ranking_run_id).

    profile_id is None (the profile-less /bulletins path): no per-profile edition to pin, so we
    build fresh at the latest run and don't materialise."""
    latest = RankingRunRepo(db).get_latest()
    if latest is None:
        return [], {}, 0
    latest_run = latest["id"]

    if profile_id is None:
        ordered = build_selection(
            db, profile_id=None, include_top_stories=include_top_stories,
            include_categories=include_categories, exclude_categories=exclude_categories, preset=preset,
        )
        return ordered, _depths_for(ordered), latest_run

    _ensure_columns(db)
    day = uk_now().date()
    row = db.execute(
        text("""SELECT story_ids, ranking_run_id FROM data.user_daily_editions
                WHERE profile_id = :p AND edition_date = :d AND request_hash = :h"""),
        {"p": profile_id, "d": day, "h": request_hash},
    ).first()

    pinned_run = None
    if row is not None and row[1] is not None:
        pinned = int(row[1])
        if latest_run <= pinned:
            pinned_run = pinned                       # no newer run → keep
        else:
            started = BulletinRepo(db).get_by_run_and_hash(pinned, request_hash) is not None
            pinned_run = pinned if started else None  # started → keep; else refresh to latest

    if pinned_run is not None:
        ids = row[0] if isinstance(row[0], list) else __import__("json").loads(row[0])
        ordered = _ordered_from_ids(db, [str(x) for x in ids])
        return ordered, _depths_for(ordered), pinned_run

    # First access, or refresh: build (runs the LLM dedup once), pin to latest, store.
    ordered = build_selection(
        db, profile_id=profile_id, include_top_stories=include_top_stories,
        include_categories=include_categories, exclude_categories=exclude_categories, preset=preset,
    )
    UserDailyEditionRepo(db).upsert(profile_id, day, request_hash, [o["story_id"] for o in ordered])
    db.execute(
        text("""UPDATE data.user_daily_editions SET ranking_run_id = :r
                WHERE profile_id = :p AND edition_date = :d AND request_hash = :h"""),
        {"r": latest_run, "p": profile_id, "d": day, "h": request_hash},
    )
    db.commit()
    return ordered, _depths_for(ordered), latest_run
