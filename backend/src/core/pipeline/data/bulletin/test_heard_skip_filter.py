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
def test_no_change_when_nothing_consumed_and_already_ordered():
    stored = ["1", "3", "4"]                      # all politics, already in order
    new_ids, changed = reconcile_edition_ids(stored, set(), ["9"], _CI, target_count=3)
    assert changed is False
    assert new_ids == stored                      # untouched — hot path skips the write


# cat_index_of mirrors filter_category_index: politics=1, business=2, technology=3, sport=8.
_CI = {
    "pol1": 1, "pol2": 1, "biz1": 2, "biz2": 2, "biz_new": 2, "tech1": 3, "sport1": 8, "sport2": 8,
    "10": 1, "12": 1, "14": 1, "20": 1, "21": 1, "22": 1,
    "1": 1, "3": 1, "4": 1,
}


def _cat_seq(ids):
    return [_CI.get(i, 10_000) for i in ids]


def test_drops_consumed_and_backfills_in_category_order():
    stored = ["10", "11", "12", "13", "14"]      # all same category (politics) here
    dropped = {"11", "13"}
    fresh = ["10", "20", "12", "21", "14", "22"]
    new_ids, changed = reconcile_edition_ids(stored, dropped, fresh, _CI, target_count=5)
    assert changed is True
    assert new_ids == ["10", "12", "14", "20", "21"]   # survivors first, then backfill (same cat)
    assert "11" not in new_ids and "13" not in new_ids
    assert len(new_ids) == 5


def test_backfill_splices_into_its_category_not_the_tail():
    # THE BLOCKER: a business backfill must land in the business section, never after sport.
    stored = ["pol1", "biz1", "tech1", "sport1"]  # a consumed business story left a gap
    dropped = {"biz1"}
    fresh = ["pol1", "tech1", "sport1", "biz_new"]  # biz_new is the backfill (business)
    new_ids, changed = reconcile_edition_ids(stored, dropped, fresh, _CI, target_count=4)
    assert changed is True
    # biz_new (business=2) sits AFTER pol1(1) and BEFORE tech1(3)/sport1(8) — not at the tail.
    assert new_ids == ["pol1", "biz_new", "tech1", "sport1"]
    assert _cat_seq(new_ids) == sorted(_cat_seq(new_ids))   # non-decreasing = fixed-order subseq


def test_heals_prior_tail_append_even_with_no_gap():
    # An edition already violated by the old buggy append (business after sport), nothing consumed
    # this read → still re-sorted into category order (self-heal), no backfill needed.
    stored = ["pol1", "biz1", "tech1", "sport1", "biz2"]   # biz2 wrongly at the tail
    new_ids, changed = reconcile_edition_ids(stored, dropped=set(), fresh_ranked_ids=[],
                                             cat_index_of=_CI, target_count=5)
    assert changed is True
    assert new_ids == ["pol1", "biz1", "biz2", "tech1", "sport1"]
    assert _cat_seq(new_ids) == sorted(_cat_seq(new_ids))


def test_already_correct_edition_is_unchanged():
    stored = ["pol1", "biz1", "tech1", "sport1"]
    new_ids, changed = reconcile_edition_ids(stored, set(), [], _CI, target_count=4)
    assert new_ids == stored
    assert changed is False


def test_invariant_category_sequence_is_subsequence_of_fixed_order():
    # For ANY reconcile result, the category-index sequence must be non-decreasing.
    stored = ["pol1", "biz1", "biz2", "tech1", "sport1", "sport2"]
    dropped = {"biz1", "tech1"}
    fresh = ["pol2", "biz_new", "sport1", "sport2"]   # mixed-category backfill candidates
    new_ids, _ = reconcile_edition_ids(stored, dropped, fresh, _CI, target_count=6)
    seq = _cat_seq(new_ids)
    assert seq == sorted(seq), f"category order violated: {seq} for {new_ids}"
