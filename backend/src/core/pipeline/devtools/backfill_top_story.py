"""
Backfill the editorial top_story flag over recent ranking-runs.

The editorial pass is cached per ranking_run_id and computed lazily the first time a bulletin
is generated for that run. Reviews cached BEFORE the top_story change carry no flags, and a
brand-new run only gets flags when its first bulletin is assembled. This devtool refreshes the
recent window so existing stories are flagged now, rather than waiting for fresh runs:

  • Target runs = every ranking-run in the last --hours that ALREADY has a cached editorial
    review (they'd otherwise serve stale, unflagged reviews), plus the LATEST run (pre-warm so
    a bulletin generated immediately has flags).
  • Clear those runs' cached reviews, run ONE fresh editorial pass over the current candidate
    set, and write the result (with top_story) under each target run.

Idempotent: safe to re-run. Dry-run by default (prints the target runs + what would be flagged);
pass --apply to write.

Usage:
    DATABASE_URL=... PYTHONPATH=src python -m core.pipeline.devtools.backfill_top_story [--hours 36] [--apply]
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.category_ranker import get_category_weight
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo
from core.pipeline.ranking.scorer import score_story
from core.pipeline.data.bulletin.editorial import _model
from core.pipeline.data.bulletin.editorial_review import EditorialRepo, editorial_adjustments


def _target_runs(db, hours: int) -> list[int]:
    """Recent runs with a cached review (need refreshing) + the latest run (pre-warm)."""
    rows = db.execute(
        text("""
            SELECT DISTINCT er.ranking_run_id
            FROM data.editorial_reviews er
            JOIN data.ranking_runs rr ON rr.id = er.ranking_run_id
            WHERE rr.created_at >= (NOW() AT TIME ZONE 'UTC') - (:h || ' hours')::interval
        """),
        {"h": hours},
    ).scalars().all()
    targets = {int(r) for r in rows}
    latest = RankingRunRepo(db).get_latest()
    if latest:
        targets.add(int(latest["id"]))
    return sorted(targets)


def run(hours: int, apply: bool) -> None:
    if not settings.enable_editorial_pass or not settings.anthropic_api_key:
        print("editorial pass disabled or ANTHROPIC_API_KEY missing — nothing to do.")
        return

    with SessionLocal() as db:
        targets = _target_runs(db, hours)
        print(f"target runs (last {hours}h with a cached review, + latest): {targets}")
        if not targets:
            print("no target runs — nothing to backfill.")
            return

        # ONE fresh editorial pass over the CURRENT candidate set. We compute it against the
        # latest run id; on --apply that call also caches it under the latest run.
        cands = load_story_ranking_candidates(db)
        scored = [score_story(c, get_category_weight(c.primary_category)) for c in cands]
        cat_by_id = {c.story_id: (c.primary_category or "—") for c in cands}
        title_by_id = {c.story_id: c.title for c in cands}

        if not apply:
            # DRY RUN: clear nothing; compute the review transiently on a throwaway run id so we
            # never touch a real run's cache, and show what would be flagged.
            adj = editorial_adjustments(db, -1, scored)
            db.execute(text("DELETE FROM data.editorial_reviews WHERE ranking_run_id = -1"))
            db.commit()
            flagged = [(sid, a) for sid, a in adj.items() if a.top_story]
            print(f"\nwould flag {len(flagged)} top_story across {len(targets)} runs:")
            for sid, a in flagged:
                print(f"  [{cat_by_id.get(sid, '?'):<22}] {title_by_id.get(sid, sid)[:64]}  — {a.reason}")
            print("\nDRY RUN — pass --apply to clear + rewrite these runs' reviews.")
            return

        # APPLY: clear the stale reviews for every target, compute once (cached under latest),
        # then copy to the other targets.
        repo = EditorialRepo(db)
        repo.ensure_table()
        for run_id in targets:
            db.execute(text("DELETE FROM data.editorial_reviews WHERE ranking_run_id = :r"), {"r": run_id})
        db.commit()

        latest_id = targets[-1] if targets else -1
        adj = editorial_adjustments(db, latest_id, scored)   # cache miss (cleared) → review + cache latest
        for run_id in targets:
            if run_id != latest_id:
                repo.put(run_id, adj, _model())

        flagged = [(sid, a) for sid, a in adj.items() if a.top_story]
        print(f"\nAPPLIED: refreshed {len(targets)} runs with {len(flagged)} top_story flags:")
        for sid, a in flagged:
            print(f"  [{cat_by_id.get(sid, '?'):<22}] {title_by_id.get(sid, sid)[:64]}  — {a.reason}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=36)
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    a = ap.parse_args()
    run(a.hours, a.apply)
