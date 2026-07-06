"""
Stable ranked list (data.ranked_stories) — insert-in-place maintenance.

The move away from continuous re-ranking: a story is scored ONCE on time-invariant merit
(compute_merit) when it first appears, INSERTED at that position, and left there. Already-
placed stories are NOT re-scored or reordered — EXCEPT the one allowed reorder: when a
story's coverage genuinely grows (source_count jumps by both an absolute and a relative
margin) it is re-scored and moved up. Stories age out of the list past 24h. Position is
ORDER BY merit_score DESC, story_id.

Phase 1: this runs ALONGSIDE the existing ranking_runs (dual-write). The request path does
not read this list yet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from .category_ranker import get_category_weight
from .config import (
    COVERAGE_GROWTH_MIN_ABS,
    COVERAGE_GROWTH_MIN_FACTOR,
    RANKED_LIST_WINDOW_HOURS,
)
from .models import StoryRankingCandidate
from .scorer import compute_merit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedListStats:
    inserted: int
    regraded: int   # re-scored + moved on genuine coverage growth
    pruned: int


class RankedStoryRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def placed_source_counts(self) -> dict[str, int]:
        """story_id -> the source_count the story was last scored at (for the growth check)."""
        rows = self.db.execute(
            text("SELECT story_id, source_count FROM data.ranked_stories")
        ).all()
        return {str(sid): int(sc) for sid, sc in rows}

    def insert_new(self, story_id: str, merit: float, category: str | None,
                   top_story: bool, source_count: int) -> None:
        # INSERT-once: a story that is already placed keeps its position (DO NOTHING), so a
        # concurrent ingest can never reset an existing row's entered_list_at or merit.
        self.db.execute(
            text("""
                INSERT INTO data.ranked_stories
                    (story_id, merit_score, primary_category, top_story, source_count)
                VALUES (:sid, :merit, :cat, :top, :sc)
                ON CONFLICT (story_id) DO NOTHING
            """),
            {"sid": int(story_id), "merit": merit, "cat": category,
             "top": top_story, "sc": source_count},
        )

    def regrade_on_growth(self, story_id: str, merit: float, top_story: bool,
                          source_count: int) -> int:
        """The ONE allowed reorder: bump a placed story's merit when coverage grew. Guarded by
        `source_count < :sc` so it only ever moves on genuine growth and is idempotent.
        entered_list_at (the 24h age) is deliberately preserved."""
        res = self.db.execute(
            text("""
                UPDATE data.ranked_stories
                   SET merit_score = :merit, source_count = :sc, top_story = :top,
                       last_scored_at = now()
                 WHERE story_id = :sid AND source_count < :sc
            """),
            {"sid": int(story_id), "merit": merit, "sc": source_count, "top": top_story},
        )
        return res.rowcount or 0

    def prune_aged(self, hours: int) -> int:
        res = self.db.execute(
            text("""
                DELETE FROM data.ranked_stories
                 WHERE entered_list_at < (now() AT TIME ZONE 'UTC') - (:h || ' hours')::interval
            """),
            {"h": hours},
        )
        return res.rowcount or 0

    def flagged_top_story_ids(self, ranking_run_id: int) -> set[str]:
        """story_ids the just-run editorial pass flagged top_story — the source of the stable
        per-story flag written on insert / coverage-growth regrade."""
        rows = self.db.execute(
            text("""SELECT story_id FROM data.editorial_reviews
                    WHERE ranking_run_id = :r AND top_story = true AND story_id <> '__reviewed__'"""),
            {"r": ranking_run_id},
        ).all()
        return {str(r[0]) for r in rows}


def _coverage_grew(placed_sc: int, new_sc: int) -> bool:
    return new_sc >= placed_sc + COVERAGE_GROWTH_MIN_ABS and new_sc >= placed_sc * COVERAGE_GROWTH_MIN_FACTOR


def maintain_ranked_list(
    db: Session, ranking_run_id: int, candidates: list[StoryRankingCandidate],
) -> RankedListStats:
    """Insert new stories at their merit position, regrade only genuinely-growing ones, prune
    aged rows. Idempotent and side-effect-contained: safe to run every ingest. `candidates`
    should already be the ~24h window."""
    repo = RankedStoryRepo(db)
    placed = repo.placed_source_counts()
    flagged = repo.flagged_top_story_ids(ranking_run_id)

    inserted = regraded = 0
    for c in candidates:
        sid = str(c.story_id)
        new_sc = int(c.source_count or 0)
        is_top = sid in flagged
        if sid not in placed:
            merit = compute_merit(c, get_category_weight(c.primary_category))
            repo.insert_new(sid, merit, c.primary_category, is_top, new_sc)
            inserted += 1
        elif _coverage_grew(placed[sid], new_sc):
            merit = compute_merit(c, get_category_weight(c.primary_category))
            regraded += repo.regrade_on_growth(sid, merit, is_top, new_sc)
        # else: placed and no genuine coverage growth → left exactly where it is.

    pruned = repo.prune_aged(RANKED_LIST_WINDOW_HOURS)
    db.commit()
    return RankedListStats(inserted=inserted, regraded=regraded, pruned=pruned)
