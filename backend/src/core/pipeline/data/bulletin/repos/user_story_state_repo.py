from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

CONSUMPTION_THRESHOLD = 0.5  # Minimum position_pct to count as consumed


def compute_new_state(action: str, position_pct: float) -> str | None:
    """
    Return the target state for a story event, or None if no DB write should occur.

    None means "abandoned below threshold" — story stays queued and eligible for
    replay after the 24h TTL. Terminal states (consumed, rejected) never returned
    here for rows that are already terminal; the repo's WHERE state='queued' guard
    enforces that separately.
    """
    if action == "completed":
        return "consumed"
    if action == "skipped":
        return "consumed" if position_pct >= CONSUMPTION_THRESHOLD else "rejected"
    if action == "abandoned":
        return "consumed" if position_pct >= CONSUMPTION_THRESHOLD else None
    return None


class UserStoryStateRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_excluded_story_ids(self, profile_id: int) -> set[int]:
        """
        Return all story_ids that should be excluded from bulletin assembly
        for this profile. Covers queued, rejected, and consumed states.
        Called after TTL cleanup so stale queued rows are already removed.
        """
        rows = self.db.execute(
            text("SELECT story_id FROM data.user_story_state WHERE profile_id = :pid"),
            {"pid": profile_id},
        ).all()
        return {r[0] for r in rows}

    def get_consumed_story_ids(self, profile_id: int) -> set[int]:
        """
        Story_ids the user has actually HEARD (state='consumed'). The daily edition
        drops these on refresh; UNHEARD stories (queued) deliberately persist across
        regenerations — so a story you haven't heard doesn't vanish on a re-roll.
        """
        rows = self.db.execute(
            text("""
                SELECT story_id FROM data.user_story_state
                WHERE profile_id = :pid AND state = 'consumed'
            """),
            {"pid": profile_id},
        ).all()
        return {r[0] for r in rows}

    def insert_queued_batch(
        self,
        *,
        profile_id: int,
        bulletin_id: int,
        story_segments: list[dict],
    ) -> None:
        """
        Insert one queued row per story segment at manifest assembly time.
        ON CONFLICT DO NOTHING — a pre-existing consumed/rejected row is never
        overwritten. A pre-existing queued row (duplicate manifest request) is
        also left unchanged.

        story_segments: list of segment dicts with 'story_id' and 'story_hash'.
        """
        for seg in story_segments:
            self.db.execute(
                text("""
                    INSERT INTO data.user_story_state
                        (profile_id, story_id, story_hash, bulletin_id, state)
                    VALUES (:profile_id, :story_id, :story_hash, :bulletin_id, 'queued')
                    ON CONFLICT (profile_id, story_id) DO NOTHING
                """),
                {
                    "profile_id": profile_id,
                    "story_id": seg["story_id"],
                    "story_hash": seg["story_hash"],
                    "bulletin_id": bulletin_id,
                },
            )

    def transition_state(
        self,
        *,
        profile_id: int,
        story_hash: str,
        new_state: str,
    ) -> bool:
        """
        Advance a queued row to new_state. The WHERE state='queued' guard makes
        consumed and rejected terminal — they cannot be overwritten.
        Returns True if a row was updated, False if it was already terminal or
        did not exist (both are acceptable outcomes).
        """
        result = self.db.execute(
            text("""
                UPDATE data.user_story_state
                SET   state      = :new_state,
                      updated_at = now()
                WHERE profile_id = :profile_id
                  AND story_hash  = :story_hash
                  AND state       = 'queued'
            """),
            {
                "profile_id": profile_id,
                "story_hash": story_hash,
                "new_state": new_state,
            },
        )
        return result.rowcount > 0

    def transition_batch(
        self,
        *,
        profile_id: int,
        events: list[dict],
    ) -> int:
        """
        Apply state transitions for multiple events (summary endpoint).
        events: list of dicts with 'story_hash', 'action', 'position_pct'.
        Returns count of rows actually updated.
        """
        updated = 0
        for ev in events:
            new_state = compute_new_state(ev["action"], ev.get("position_pct", 0.0))
            if new_state is None:
                continue
            if self.transition_state(
                profile_id=profile_id,
                story_hash=ev["story_hash"],
                new_state=new_state,
            ):
                updated += 1
        return updated

    def cleanup_stale_queued(self, profile_id: int) -> int:
        """
        Delete queued rows older than 24 hours for this profile.
        Called at the start of every manifest request before assembly.
        Returns count of rows deleted (stories reverted to eligible).
        """
        result = self.db.execute(
            text("""
                DELETE FROM data.user_story_state
                WHERE profile_id = :profile_id
                  AND state       = 'queued'
                  AND created_at  < now() - INTERVAL '24 hours'
            """),
            {"profile_id": profile_id},
        )
        return result.rowcount
