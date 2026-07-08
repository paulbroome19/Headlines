"""Live heard/skip filter on the materialised-selection path (audit §8 — previously UNTESTED).

Covers:
 - the state machine: what a play/skip/abandon event resolves to (spec: skip = consumed).
 - reconcile_edition_ids: the pull-to-refresh living-edition rule — drop consumed, keep unconsumed
   in place (no reshuffle), backfill the gaps from current rankings, never grow past the count.
"""
from core.pipeline.data.bulletin.repos.user_story_state_repo import compute_new_state
from core.pipeline.data.bulletin.selection import reconcile_edition_ids


# ── State machine (spec alignment) ──────────────────────────────────────────────────────────
def test_fully_played_is_consumed():
    assert compute_new_state("completed", 1.0) == "consumed"


def test_skip_is_consumed_at_any_position():
    # SPEC: skip = not interested = consumed, regardless of how far in.
    assert compute_new_state("skipped", 0.0) == "consumed"
    assert compute_new_state("skipped", 0.05) == "consumed"
    assert compute_new_state("skipped", 0.9) == "consumed"


def test_abandon_is_consumed_only_past_halfway():
    # Passive drop-off (backgrounded/closed), not an active skip.
    assert compute_new_state("abandoned", 0.8) == "consumed"
    assert compute_new_state("abandoned", 0.2) is None      # stays queued / eligible
    assert compute_new_state("unknown", 1.0) is None


# ── reconcile_edition_ids (pull-to-refresh) ─────────────────────────────────────────────────
def test_no_change_when_nothing_consumed():
    stored = ["1", "2", "3", "4"]
    new_ids, changed = reconcile_edition_ids(stored, dropped=set(), fresh_ranked_ids=["9"], target_count=4)
    assert changed is False
    assert new_ids == stored                     # untouched — hot path skips the ranking query/write


def test_drops_consumed_and_backfills_preserving_survivor_order():
    stored = ["10", "11", "12", "13", "14"]      # the pinned edition
    dropped = {"11", "13"}                        # heard/skipped since it was pinned
    fresh = ["10", "20", "12", "21", "14", "22"] # current rankings (top-first)
    new_ids, changed = reconcile_edition_ids(stored, dropped, fresh, target_count=5)
    assert changed is True
    # survivors keep their ORDER and positions; backfill fills the 2 freed slots from ranked order
    assert new_ids[:3] == ["10", "12", "14"]     # no reshuffle of the unconsumed
    assert new_ids == ["10", "12", "14", "20", "21"]
    assert "11" not in new_ids and "13" not in new_ids
    assert len(new_ids) == 5                      # count restored, never grows


def test_backfill_excludes_dropped_and_already_present():
    stored = ["1", "2", "3"]
    dropped = {"2"}
    fresh = ["1", "2", "3", "4"]                  # 1,3 present; 2 dropped → only 4 is eligible
    new_ids, _ = reconcile_edition_ids(stored, dropped, fresh, target_count=3)
    assert new_ids == ["1", "3", "4"]


def test_count_shrinks_when_not_enough_backfill():
    stored = ["1", "2", "3", "4"]
    dropped = {"2", "4"}
    fresh = ["1", "3"]                            # nothing new to backfill with
    new_ids, changed = reconcile_edition_ids(stored, dropped, fresh, target_count=4)
    assert changed is True
    assert new_ids == ["1", "3"]                  # count shrinks rather than re-adding dropped
