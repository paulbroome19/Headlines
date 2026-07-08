"""ONE membership: when final assembly drops a summary-less story, the edition (screen) is
reconciled to the ASSEMBLED set so screen == skeleton == audio. Reconstructs the real Liza/124395
case — a story with prior-run summaries but none for the play run.

Pure (no DB): a fake Session captures the emitted UPDATE + params so we can assert removal-only,
order-preserving, update-not-insert behaviour and the no-op-when-absent contract.
"""
from __future__ import annotations

import json
from datetime import date

from core.pipeline.data.bulletin.edition import UserDailyEditionRepo


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount: int = 1) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._rowcount = rowcount

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        return _Result(self._rowcount)


def test_reconcile_removes_dropped_story_preserving_order():
    # Liza edition 37cbc: 124395 ("Legionnaires'") had prior-run summaries but none for run 4515,
    # so assembly dropped it from audio. Reconcile updates the edition to the assembled set.
    edition_ids = ["920", "248689", "243378", "1511", "275342", "308003", "247784", "271696", "124395"]
    assembled   = ["920", "248689", "243378", "1511", "275342", "308003", "247784", "271696"]  # 124395 gone

    sess = _FakeSession(rowcount=1)
    n = UserDailyEditionRepo(sess).reconcile_story_ids(24, date(2026, 7, 8), "37cbc92bff8f9853", assembled)

    assert n == 1
    sql, params = sess.calls[-1]
    assert "UPDATE data.user_daily_editions" in sql
    assert "INSERT" not in sql                       # update/removal only — never creates a row
    stored = json.loads(params["ids"])
    assert stored == assembled                       # order preserved, 124395 removed
    assert "124395" not in stored
    assert set(stored).issubset(set(edition_ids))    # strictly removal — never adds
    assert len(stored) == len(assembled)             # counts agree: screen == audio


def test_reconcile_is_a_noop_when_no_edition_row_exists():
    # Cold play-first (no preview ever materialised) → WHERE matches nothing → 0 rows, no create.
    sess = _FakeSession(rowcount=0)
    n = UserDailyEditionRepo(sess).reconcile_story_ids(1, date(2026, 7, 8), "deadbeef", ["1", "2"])
    assert n == 0


def test_reconcile_idempotent_when_already_equal():
    # Nothing dropped → the assembled set equals the edition; reconcile is a harmless self-update.
    ids = ["5074", "58530", "240779"]
    sess = _FakeSession(rowcount=1)
    UserDailyEditionRepo(sess).reconcile_story_ids(22, date(2026, 7, 8), "abc", ids)
    _, params = sess.calls[-1]
    assert json.loads(params["ids"]) == ids
