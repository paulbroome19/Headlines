from __future__ import annotations

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.events import DATA_RANK_COMPLETED
from core.pipeline.ranking.ranker import StoryRanker
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo


def handle_categorise_completed(event: dict) -> None:
    """
    Triggered by: data.categorise.completed

    Responsibilities:
    - Load all recent story candidates (last 48h) from the DB
    - Run StoryRanker to produce top_stories + briefing
    - Persist the result to data.ranking_runs
    - Emit data.rank.completed (same transaction)

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
        run_id = repo.insert_run(
            batch_id=categorisation_batch_id,
            candidate_count=len(candidates),
            top_stories=result.top_stories,
            briefing=result.briefing,
        )

        outbox = OutboxRepo(db)
        outbox.add_event(
            Event(
                type=DATA_RANK_COMPLETED,
                idempotency_key=f"rank.completed:{categorisation_batch_id}",
                payload={
                    "ranking_run_id": run_id,
                    "ranking_batch_id": categorisation_batch_id,
                    "categorisation_batch_id": categorisation_batch_id,
                    "top_story_ids": [s.story_id for s in result.top_stories],
                    "briefing_story_ids": [s.story_id for s in result.briefing],
                },
            )
        )

        db.commit()
