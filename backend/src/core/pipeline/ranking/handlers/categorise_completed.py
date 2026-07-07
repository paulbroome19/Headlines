from __future__ import annotations

import logging

from core.platform.db.session import SessionLocal

from core.pipeline.data.bulletin.editorial_review import precompute_editorial_for_run
from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.category_ranker import get_category_weight
from core.pipeline.ranking.config import RANKED_LIST_WINDOW_HOURS
from core.pipeline.ranking.ranked_list import maintain_ranked_list
from core.pipeline.ranking.ranker import StoryRanker
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo
from core.pipeline.ranking.scorer import score_story

logger = logging.getLogger(__name__)


def handle_categorise_completed(event: dict) -> None:
    """
    Triggered by: data.categorise.completed

    Responsibilities:
    - Load all recent story candidates (last 48h) from the DB
    - Run StoryRanker to produce top_stories + briefing, persist to data.ranking_runs
      (read by the cached-bulletin path + /ranking endpoints)
    - Precompute the editorial top_story flags and maintain the stable ranked list

    This is where the event-driven INGEST cascade ends; the generate path (summarise →
    assemble → TTS) runs inline in routes/data.py, not via events. Ranking is global: it
    considers all recent stories. categorisation_batch_id is used only for idempotency.
    """
    payload = event.get("payload") or {}
    categorisation_batch_id = payload.get("categorisation_batch_id") or ""

    ranker = StoryRanker()

    with SessionLocal() as db:
        candidates = load_story_ranking_candidates(db, hours=48)

        result = ranker.rank(candidates, top_stories_limit=5, briefing_limit=15)

        repo = RankingRunRepo(db)
        run_id = repo.insert_run(
            batch_id=categorisation_batch_id,
            candidate_count=len(candidates),
            top_stories=result.top_stories,
            briefing=result.briefing,
        )

        # Commit the ranking run first — the editorial precompute below is best-effort and must
        # never be able to roll it back. (The old data.rank.completed emit had no consumer —
        # summarisation is lazy in routes/data.py — and was removed: audit §6/§7 B.2.)
        db.commit()

        # Precompute the editorial top_story flags for this run, off the request path, so BOTH
        # assembly paths (blocking + streaming skeleton) read them from cache and gate the front
        # page identically — the core fix for the streaming path bypassing the significance gate.
        # Best-effort + burst-deduped (one Haiku review per candidate set); never breaks ranking.
        try:
            scored = [score_story(c, get_category_weight(c.primary_category)) for c in candidates]
            precompute_editorial_for_run(db, run_id, scored)
        except Exception:
            logger.exception("editorial precompute skipped for ranking run %s", run_id)

        # STABLE RANKED LIST (data.ranked_stories) — Phase 1 dual-write, ALONGSIDE the run.
        # Score new stories once on time-invariant merit, insert in place, regrade only on
        # genuine coverage growth, prune past 24h. Runs after the editorial precompute so the
        # per-story top_story flag is available. Best-effort; never breaks ranking. The request
        # path does NOT read this list yet (that's the next phase).
        try:
            list_candidates = load_story_ranking_candidates(db, hours=RANKED_LIST_WINDOW_HOURS)
            stats = maintain_ranked_list(db, run_id, list_candidates)
            logger.info("ranked_list: run=%s inserted=%d regraded=%d restamped=%d pruned=%d",
                        run_id, stats.inserted, stats.regraded, stats.restamped, stats.pruned)
        except Exception:
            logger.exception("ranked_list maintenance skipped for run %s", run_id)
