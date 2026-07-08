"""
Store for the materialised per-(profile, edition_date, request_hash) selection.

This holds the ordered story-id set for a user's edition of the day so the home preview
and the briefing render the SAME selection (see bulletin.selection, PR #157). The repo
below is the IO; the selection logic + freshness pinning live in bulletin.selection.

(The old reconcile/splice/refill machinery — reconcile_edition, select_edition_survivors,
resolve_daily_edition — and the read_from_ranked_list=false live-scoring path were removed:
audit §7 B.8/B.9. The stable ranked list is now the only selection path.)
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session


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

    def reconcile_story_ids(
        self, profile_id: int, edition_date: date, request_hash: str, story_ids: list[str]
    ) -> int:
        """Update an EXISTING edition's story_ids to the assembled set — the ONE-membership rule:
        when final assembly drops a story (no summary row for the play run), it must also leave the
        screen, so screen == skeleton == audio. Removal-only in practice: `story_ids` is the
        assembled order, a subsequence of the pinned edition, so this never reorders or adds. No-op
        (returns 0) if no edition row exists (e.g. a cold play-first that never materialised a
        preview); the pinned ranking_run_id is untouched. Idempotent."""
        result = self.db.execute(
            text("""
                UPDATE data.user_daily_editions
                   SET story_ids = CAST(:ids AS jsonb), updated_at = now()
                 WHERE profile_id = :pid AND edition_date = :d AND request_hash = :h
            """),
            {"pid": profile_id, "d": edition_date, "h": request_hash,
             "ids": json.dumps([str(x) for x in story_ids])},
        )
        return result.rowcount

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


