from __future__ import annotations

from core.platform.db.session import SessionLocal

from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.ranker import StoryRanker
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo


def handle_categorise_completed(event: dict) -> None:
    """
    Triggered by: data.categorise.completed

    Responsibilities:
    - Load all recent story candidates (last 48h) from the DB
    - Run StoryRanker to produce top_stories + briefing
    - Persist the result to data.ranking_runs

    Ranking is global: it considers all recent stories, not just those in
    this batch. The categorisation_batch_id is used only for idempotency.
    """
    payload = event.get("payload") or {}
    categorisation_batch_id = payload.get("categorisation_batch_id") or ""

    ranker = StoryRanker()

    with SessionLocal() as db:
        candidates = load_story_ranking_candidates(db, hours=48)

        result = ranker.rank(candidates, top_stories_limit=5, briefing_limit=15)

        repo = RankingRunRepo(db)
        repo.insert_run(
            batch_id=categorisation_batch_id,
            candidate_count=len(candidates),
            top_stories=result.top_stories,
            briefing=result.briefing,
        )

        db.commit()
