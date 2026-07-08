"""Consumption is keyed on STORY_ID, not story_hash (build-45 regression).

A story's script_hash drifts across bulletins — when it becomes the split lead (opener+remainder)
or is re-summarised — while the queued row is ON CONFLICT DO NOTHING, so it keeps its FIRST hash.
Keying the transition on the hash therefore silently failed to consume such stories (Farage played
as the lead never advanced from 'queued'). These tests pin that the transition now filters by
story_id and advances regardless of the hash.

Pure — a fake session captures the emitted SQL + params (no DB).
"""
from core.pipeline.data.bulletin.repos.user_story_state_repo import (
    UserStoryStateRepo,
    compute_new_state,
)


class _Result:
    def __init__(self, rowcount): self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount=1):
        self.calls = []
        self._rowcount = rowcount

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        return _Result(self._rowcount)


def test_transition_state_filters_by_story_id_not_hash():
    sess = _FakeSession(rowcount=1)
    ok = UserStoryStateRepo(sess).transition_state(profile_id=22, story_id="240779", new_state="consumed")
    assert ok is True
    sql, params = sess.calls[-1]
    assert "story_id" in sql and "story_id" in params
    assert "story_hash" not in sql          # the fragile key is gone
    assert "state       = 'queued'" in sql or "state = 'queued'" in sql
    assert params["story_id"] == "240779" and params["new_state"] == "consumed"


def test_transition_batch_advances_by_story_id_ignoring_hash_drift():
    # Farage as the split lead: the event carries story_id 240779; the queued row's stored hash is
    # stale (from a morning bulletin) — irrelevant now, the UPDATE matches on story_id.
    sess = _FakeSession(rowcount=1)
    n = UserStoryStateRepo(sess).transition_batch(
        profile_id=22,
        events=[{"story_id": "240779", "story_hash": "560756d8909610bb", "action": "skipped", "position_pct": 0.0}],
    )
    assert n == 1
    _, params = sess.calls[-1]
    assert params["story_id"] == "240779"
    assert params["new_state"] == "consumed"        # skip = consumed (any position)


def test_transition_batch_skips_events_without_resolved_story_id():
    # An event whose story_id couldn't be resolved (old client + unknown hash) is skipped, not crashed.
    sess = _FakeSession(rowcount=1)
    n = UserStoryStateRepo(sess).transition_batch(
        profile_id=22,
        events=[{"story_hash": "deadbeef", "action": "completed", "position_pct": 1.0}],  # no story_id
    )
    assert n == 0
    assert sess.calls == []                          # no UPDATE attempted


def test_skip_maps_to_consumed_which_is_dropped():
    # Regression guard: skip → consumed (dropped by the read filter, which drops consumed ∪ rejected).
    assert compute_new_state("skipped", 0.0) == "consumed"
    assert compute_new_state("completed", 1.0) == "consumed"
