"""
Stable per-user DAILY EDITION of the bulletin.

A user's bulletin should be their edition of the day, not a fresh random draw each
tap. Without this, every regenerate re-scores from scratch against a live-updating
DB (a new ranking run lands every few minutes), so the set churns — 2 stories, then
7, then 1 — and an unheard story can vanish just because the re-roll scored slightly
differently.

Mechanism: persist the ordered story-id set per (profile, edition_date, request_hash)
in data.user_daily_editions. On each request:
  • First edition of the day  → use the fresh selection, persist it.
  • Subsequent regenerations   → REUSE the stored edition, but
      - drop stories the user has actually HEARD (consumed); UNHEARD stories persist,
      - refill freed slots from the current best unheard candidates,
      - splice a genuinely bigger NEW lead (the current #1) to the front so breaking
        news still surfaces,
      - never re-roll the already-chosen unheard set on a whim.

Edition boundary = the user's local (UK) date (uk_now), so it resets at local midnight.
The reconcile step is a pure function (unit-testable); the repo + resolver do the IO.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.pipeline.data.bulletin.connective import uk_now
from core.pipeline.data.bulletin.repos.user_story_state_repo import UserStoryStateRepo
from core.pipeline.ranking.depth import depth_for_rank

# Absolute ceiling on stories held in an edition — a safety cap only. The effective
# size is the caller's preset target (Quick/Standard/Deep); this just bounds it so a
# huge target can never blow up the edition. Set to the largest preset target (Deep=16).
EDITION_MAX = 16


def reconcile_edition(
    existing_ids: list[str],
    fresh_ids: list[str],
    heard_ids: set[str],
    *,
    max_size: int = EDITION_MAX,
) -> list[str]:
    """
    Reconcile a stored edition against the current fresh selection.

    existing_ids — the edition's stored ordered story ids (strings).
    fresh_ids    — the current selection's ordered story ids (best first).
    heard_ids    — story ids the user has consumed (as strings).

    Returns the new ordered id list:
      - KEEP existing unheard stories in their stored order (stability).
      - SPLICE the current top story to the front if it's new (not kept, not heard) —
        genuinely bigger news leads.
      - REFILL freed (heard) slots and fill up to max_size with the current best new
        stories, in fresh order.
    Deterministic; never drops an unheard kept story except to the size cap.
    """
    heard = set(heard_ids)
    kept = [sid for sid in existing_ids if sid not in heard]  # stable, unheard persist
    seen = set(kept)

    final: list[str] = list(kept)
    # Breaking-news lead: if the current #1 is new, put it first.
    if fresh_ids:
        lead = fresh_ids[0]
        if lead not in heard and lead not in seen:
            final.insert(0, lead)
            seen.add(lead)

    # Refill freed slots + top up to the cap with current best new stories.
    for sid in fresh_ids:
        if len(final) >= max_size:
            break
        if sid not in heard and sid not in seen:
            final.append(sid)
            seen.add(sid)

    return final[:max_size]


class UserDailyEditionRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_story_ids(self, profile_id: int, edition_date: date, request_hash: str) -> list[str] | None:
        row = self.db.execute(
            text("""
                SELECT story_ids FROM data.user_daily_editions
                WHERE profile_id = :pid AND edition_date = :d AND request_hash = :h
            """),
            {"pid": profile_id, "d": edition_date, "h": request_hash},
        ).first()
        if row is None:
            return None
        ids = row[0]
        if not isinstance(ids, list):
            ids = json.loads(ids)
        return [str(x) for x in ids]

    def upsert(self, profile_id: int, edition_date: date, request_hash: str, story_ids: list[str]) -> None:
        self.db.execute(
            text("""
                INSERT INTO data.user_daily_editions
                    (profile_id, edition_date, request_hash, story_ids, updated_at)
                VALUES (:pid, :d, :h, CAST(:ids AS jsonb), now())
                ON CONFLICT (profile_id, edition_date, request_hash)
                DO UPDATE SET story_ids = EXCLUDED.story_ids, updated_at = now()
            """),
            {"pid": profile_id, "d": edition_date, "h": request_hash, "ids": json.dumps([str(x) for x in story_ids])},
        )


def _categories_for(db: Session, story_ids: list[str]) -> dict[str, str | None]:
    """Primary category per story id — from data.stories (covers ids that dropped out
    of the fresh selection but are kept in the edition)."""
    if not story_ids:
        return {}
    rows = db.execute(
        text("SELECT id::text, primary_category FROM data.stories WHERE id::text = ANY(:ids)"),
        {"ids": story_ids},
    ).all()
    return {r[0]: r[1] for r in rows}


def resolve_daily_edition(
    db: Session,
    *,
    profile_id: int,
    request_hash: str,
    fresh_ordered: list[dict],
    fresh_depths: dict[str, tuple[str, int]],
    target: int,
) -> tuple[list[dict], dict[str, tuple[str, int]]]:
    """
    Return the stable (ordered, depths) for this profile's edition of the day,
    reconciled against the fresh selection, and persist it. Same shape as
    _threshold_select_with_depth so the downstream assembly is unchanged.

    target — the preset's story count (Quick/Standard/Deep). The edition is capped to
    it AFTER dropping already-heard/skipped stories, so freed slots refill from the
    deeper reservoir in `fresh_ordered` (which the caller sized larger than target)
    rather than shrinking the bulletin. Bounded by EDITION_MAX as a safety ceiling.
    """
    cap = min(target, EDITION_MAX)
    edition_date = uk_now().date()
    repo = UserDailyEditionRepo(db)
    existing = repo.get_story_ids(profile_id, edition_date, request_hash)

    fresh_ids = [o["story_id"] for o in fresh_ordered]
    # "Done with" = heard (consumed) OR skipped-early (rejected). Filter these EVERYWHERE
    # — including the first edition of a new day — so a story the user has genuinely dealt
    # with never comes back (across regenerations AND across the day boundary), while
    # unheard (queued) stories still persist.
    dropped = {str(x) for x in UserStoryStateRepo(db).get_dropped_story_ids(profile_id)}

    # Cap AFTER dropping (not before) so heard/skipped stories are replaced from the
    # reservoir's deeper candidates — the target is a length target, not a hard slice of
    # the top-N that erodes as the user works through it.
    if existing is None:
        final_ids = [sid for sid in fresh_ids if sid not in dropped][:cap]
    else:
        final_ids = reconcile_edition(existing, fresh_ids, dropped, max_size=cap)

    repo.upsert(profile_id, edition_date, request_hash, final_ids)

    # Rebuild (ordered, depths) for the final set. Category comes from the fresh
    # selection where present, else from data.stories (kept-but-not-in-fresh).
    cat_by_id: dict[str, str | None] = {o["story_id"]: o.get("primary_category") for o in fresh_ordered}
    missing = [sid for sid in final_ids if sid not in cat_by_id]
    if missing:
        cat_by_id.update(_categories_for(db, missing))

    ordered: list[dict] = []
    depths: dict[str, tuple[str, int]] = {}
    for rank, sid in enumerate(final_ids):
        ordered.append({"story_id": sid, "primary_category": cat_by_id.get(sid)})
        depths[sid] = depth_for_rank(rank)
    return ordered, depths
