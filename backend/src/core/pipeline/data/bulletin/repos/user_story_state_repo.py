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
        # SPEC: a skip = "not interested" = consumed, at ANY position. The listener made an active
        # choice to move on, so the story must not recur (was: early skip → 'rejected'; that was a
        # distinct state nothing else read — both 'consumed' and 'rejected' were already dropped —
        # so this only makes the intent explicit and unambiguous).
        return "consumed"
    if action == "abandoned":
        # Passive drop-off (backgrounded / closed mid-play), NOT an active skip. Only counts as
        # heard if they got most of the way through; otherwise it stays queued and eligible.
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

    def get_dropped_story_ids(self, profile_id: int) -> set[int]:
        """
        Story_ids the user is DONE with — heard (consumed) OR actively skipped early
        (rejected). The daily edition drops BOTH so a heard story never comes back and
        an early-skipped one doesn't keep recurring; only genuinely unheard (queued)
        stories persist. Terminal states, so this is stable across the day/boundary.
        """
        rows = self.db.execute(
            text("""
                SELECT story_id FROM data.user_story_state
                WHERE profile_id = :pid AND state IN ('consumed', 'rejected')
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
        story_id: str,
        new_state: str,
    ) -> bool:
        """
        Advance a queued row to new_state, keyed on STORY_ID (not story_hash). A story's
        script_hash changes across bulletins — when it becomes the split lead (opener+remainder),
        or is re-summarised — while `insert_queued_batch` is ON CONFLICT (profile_id, story_id) DO
        NOTHING, so the queued row keeps its FIRST hash forever. Keying the transition on the hash
        therefore silently failed to consume any such story (the build-45 regression). Story
        IDENTITY is the correct key: "the user heard/skipped THIS story", regardless of which
        content version's audio played. The WHERE state='queued' guard keeps consumed/rejected
        terminal. Returns True if a row was updated.
        """
        result = self.db.execute(
            text("""
                UPDATE data.user_story_state
                SET   state      = :new_state,
                      updated_at = now()
                WHERE profile_id = :profile_id
                  AND story_id    = :story_id
                  AND state       = 'queued'
            """),
            {
                "profile_id": profile_id,
                "story_id": str(story_id),
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
        events: list of dicts with 'story_id', 'action', 'position_pct'. Events without a resolved
        story_id are skipped (the endpoint resolves it from story_hash via the bulletin segments).
        Returns count of rows actually updated.
        """
        updated = 0
        for ev in events:
            sid = ev.get("story_id")
            if not sid:
                continue
            new_state = compute_new_state(ev["action"], ev.get("position_pct", 0.0))
            if new_state is None:
                continue
            if self.transition_state(
                profile_id=profile_id,
                story_id=str(sid),
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
