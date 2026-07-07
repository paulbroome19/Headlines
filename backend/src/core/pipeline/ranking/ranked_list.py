"""
Stable ranked list (data.ranked_stories) — insert-in-place maintenance.

The move away from continuous re-ranking: a story is scored ONCE on time-invariant merit
(compute_merit) when it first appears, INSERTED at that position, and left there. Already-
placed stories are NOT re-scored or reordered — EXCEPT the one allowed reorder: when a
story's coverage genuinely grows (source_count jumps by both an absolute and a relative
margin) it is re-scored and moved up. Stories age out of the list past 24h. Position is
ORDER BY merit_score DESC, story_id.

The stable list stops rank CHURN (positions shuffling every ~15 min); it is NOT meant to
freeze editorial VERDICTS. So the top_story flag — a front-page GATE, not an ordering signal
— is re-stamped on EVERY placed row from the latest editorial verdict on every reviewed run,
unconditionally (not growth-gated). merit_score is untouched by that re-stamp, so it can
never reorder the list. (Merit itself only moves on coverage growth — see regrade_on_growth.)

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
from .models import ScoredStory, StoryRankingCandidate
from .scorer import compute_merit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedListStats:
    inserted: int
    regraded: int    # re-scored + moved on genuine coverage growth
    restamped: int   # top_story flags flipped to the latest editorial verdict (no reorder)
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

    def load_map(self) -> dict[str, tuple[float, bool]]:
        """story_id -> (persisted merit_score, stable top_story flag) for the request path."""
        rows = self.db.execute(
            text("SELECT story_id, merit_score, top_story FROM data.ranked_stories")
        ).all()
        return {str(sid): (float(m), bool(t)) for sid, m, t in rows}

    def flagged_top_story_ids(self, ranking_run_id: int) -> set[str]:
        """story_ids the just-run editorial pass flagged top_story — the source of the stable
        per-story flag written on insert and re-stamped every reviewed run."""
        rows = self.db.execute(
            text("""SELECT story_id FROM data.editorial_reviews
                    WHERE ranking_run_id = :r AND top_story = true AND story_id <> '__reviewed__'"""),
            {"r": ranking_run_id},
        ).all()
        return {str(r[0]) for r in rows}

    def run_reviewed(self, ranking_run_id: int) -> bool:
        """True once the editorial pass has recorded a verdict for this run (its `__reviewed__`
        marker row exists). The unconditional flag re-stamp is gated on this so a run whose
        editorial precompute failed or was skipped can NEVER wipe every flag to False."""
        row = self.db.execute(
            text("""SELECT 1 FROM data.editorial_reviews
                    WHERE ranking_run_id = :r AND story_id = '__reviewed__' LIMIT 1"""),
            {"r": ranking_run_id},
        ).first()
        return row is not None

    def restamp_top_story(self, flagged: set[str]) -> int:
        """Re-stamp top_story on EVERY placed row to this run's editorial verdict: True iff the
        story is flagged, False otherwise. UNCONDITIONAL — not growth-gated. This only writes
        `top_story`, never `merit_score`, so it cannot change positions (ORDER BY merit_score
        DESC, story_id); and it only touches rows whose flag actually changes. Returns the number
        of flags flipped. Callers MUST gate this on run_reviewed() so an unreviewed run can't
        clear every flag."""
        res = self.db.execute(
            text("""UPDATE data.ranked_stories
                       SET top_story = (story_id::text = ANY(:flagged))
                     WHERE top_story IS DISTINCT FROM (story_id::text = ANY(:flagged))"""),
            {"flagged": list(flagged)},
        )
        return res.rowcount or 0


def scored_from_ranked_list(
    db: Session, candidates: list[StoryRankingCandidate],
) -> list[ScoredStory]:
    """Build the request-path ScoredStory reservoir from the STABLE list instead of scoring
    live. A candidate's normalized_score is its PERSISTED merit_score (time-invariant,
    insert-in-place, coverage-growth-only reorder) and its top_story is the list's stable
    flag — neither is recomputed per request, so positions no longer swing with recency.

    Hydration-only fields (category, geo_region, pool_country, last_seen_at) come from the live
    candidate; nothing about them is re-scored. Candidates not yet in the list (arrived since
    the last ingest) are omitted — they enter on the next rank run, never mid-request.
    """
    ranked = RankedStoryRepo(db).load_map()
    out: list[ScoredStory] = []
    for c in candidates:
        row = ranked.get(str(c.story_id))
        if row is None:
            continue
        merit, top = row
        c.top_story = top
        out.append(ScoredStory(
            candidate=c,
            within_category_score=0.0, full_score=0.0, category_weight=0.0,
            recency_score=0.0, entity_weight=0.0, source_weight=0.0, cluster_weight=0.0,
            normalized_score=merit,
        ))
    return out


def _coverage_grew(placed_sc: int, new_sc: int) -> bool:
    return new_sc >= placed_sc + COVERAGE_GROWTH_MIN_ABS and new_sc >= placed_sc * COVERAGE_GROWTH_MIN_FACTOR


def top_story_flags(placed_ids: set[str], flagged: set[str]) -> dict[str, bool]:
    """Pure: the correct top_story flag for every placed story given THIS run's editorial
    verdict — True iff the story is in the run's flagged set. The flag is a front-page GATE,
    not an ordering signal, and it is recomputed from the latest verdict every reviewed run,
    so a story the editor has reversed on (e.g. demoted once its coverage looked incremental)
    stops leading on the very next run instead of freezing at whatever it was when it entered
    the list. restamp_top_story is the bulk-SQL equivalent of applying this map."""
    return {sid: (sid in flagged) for sid in placed_ids}


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
        # else: placed and no genuine coverage growth → merit left exactly where it is.

    # Re-stamp the editorial GATE on EVERY placed row from this run's verdict — unconditionally,
    # NOT growth-gated. top_story bypasses the bar and all caps, so a stale flag is maximally
    # consequential; but it is a gate, not an ordering signal, and merit is untouched, so the
    # re-stamp cannot churn positions. Skipped when the run had no editorial review, so a failed
    # precompute can never wipe every flag to False.
    restamped = repo.restamp_top_story(flagged) if repo.run_reviewed(ranking_run_id) else 0

    pruned = repo.prune_aged(RANKED_LIST_WINDOW_HOURS)
    db.commit()
    return RankedListStats(inserted=inserted, regraded=regraded, restamped=restamped, pruned=pruned)
