from __future__ import annotations

import json
import logging

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.bulletin.assembler import assemble
from core.pipeline.data.bulletin.events import DATA_BULLETIN_ASSEMBLED
from core.pipeline.data.bulletin.repos.bulletin_repo import BulletinRepo
from core.pipeline.data.bulletin.selector import compute_request_hash
from core.pipeline.data.summarise.repos.story_summary_repo import StorySummaryRepo
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo

logger = logging.getLogger(__name__)

_GLOBAL_FILTERS: dict = {}


def handle_summarise_completed(event: dict) -> None:
    """
    Triggered by: data.summarise.completed

    Assembles the global bulletin (no category filters) from existing summaries.
    No LLM calls — pure template-based assembly.
    Idempotent: skip if bulletin already exists for (ranking_run_id, request_hash).
    """
    payload = event.get("payload") or {}
    ranking_run_id = payload.get("ranking_run_id")
    ranking_batch_id = payload.get("ranking_batch_id") or ""
    trace_id = event.get("trace_id")

    if ranking_run_id is None:
        return

    request_hash = compute_request_hash(_GLOBAL_FILTERS)

    with SessionLocal() as db:
        bulletin_repo = BulletinRepo(db)

        if bulletin_repo.get_by_run_and_hash(ranking_run_id, request_hash) is not None:
            logger.info(
                "handle_summarise_completed: bulletin already exists for run=%s — skipping",
                ranking_run_id,
            )
            return

        run = RankingRunRepo(db).get_by_id(ranking_run_id)
        if run is None:
            logger.warning(
                "handle_summarise_completed: ranking_run %s not found", ranking_run_id,
            )
            return

        top = run["top_stories"] if isinstance(run["top_stories"], list) else json.loads(run["top_stories"])
        briefing = run["briefing"] if isinstance(run["briefing"], list) else json.loads(run["briefing"])

        seen: set[str] = set()
        ordered: list[dict] = []
        for s in top + briefing:
            sid = str(s["story_id"])
            if sid not in seen:
                seen.add(sid)
                ordered.append({
                    "story_id": sid,
                    "primary_category": s.get("primary_category"),
                })

        summaries_by_id: dict[str, dict] = {
            str(s["story_id"]): s
            for s in StorySummaryRepo(db).get_by_ranking_run(ranking_run_id)
        }

        stories: list[dict] = []
        missing: list[str] = []
        for item in ordered:
            sid = item["story_id"]
            summary = summaries_by_id.get(sid)
            if summary is None:
                missing.append(sid)
                continue
            stories.append({
                "story_id": sid,
                "audio_script": (summary.get("audio_script") or "").strip()
                    or (summary.get("summary_text") or ""),
                "summary_text": summary.get("summary_text") or "",
                "primary_category": item["primary_category"],
            })

        if missing:
            logger.warning(
                "handle_summarise_completed run=%s: %d stories missing summaries: %s",
                ranking_run_id, len(missing), missing,
            )

        if not stories:
            logger.warning(
                "handle_summarise_completed run=%s: no stories with summaries — skipping",
                ranking_run_id,
            )
            return

        result = assemble(stories, seed=int(request_hash, 16))

        bulletin_repo.insert(
            ranking_run_id=ranking_run_id,
            request_hash=request_hash,
            filters=_GLOBAL_FILTERS,
            story_count=len(stories),
            script=result.script,
            segments=result.segments,
        )
        OutboxRepo(db).add_event(Event(
            type=DATA_BULLETIN_ASSEMBLED,
            idempotency_key=f"bulletin.assembled:{ranking_batch_id}",
            payload={
                "ranking_run_id": ranking_run_id,
                "ranking_batch_id": ranking_batch_id,
                "story_count": len(stories),
            },
            trace_id=trace_id,
        ))
        db.commit()

    logger.info(
        "handle_summarise_completed: global bulletin assembled  run=%s  stories=%d",
        ranking_run_id, len(stories),
    )
