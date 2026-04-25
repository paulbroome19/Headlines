from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.summarise.events import DATA_SUMMARISE_COMPLETED
from core.pipeline.data.summarise.llm_summariser import summarise_story, _model
from core.pipeline.data.summarise.repos.story_summary_repo import StorySummaryRepo

logger = logging.getLogger(__name__)


def handle_rank_completed(event: dict) -> None:
    """
    Triggered by: data.rank.completed

    Cost control:
    - Deduplicates story_ids before any LLM work
    - Skips stories already summarised for this ranking_run_id
    - Reuses cached summaries from previous runs when story content is unchanged
      (matched by story_id + content_hash + model)
    - Only calls the LLM for stories with no usable cache hit

    Emit counts: attempted / generated / reused / failed
    """
    payload = event.get("payload") or {}
    ranking_run_id = payload.get("ranking_run_id")
    ranking_batch_id = payload.get("ranking_batch_id") or ""
    top_story_ids: list[str] = payload.get("top_story_ids") or []
    briefing_story_ids: list[str] = payload.get("briefing_story_ids") or []
    trace_id = event.get("trace_id")

    if ranking_run_id is None:
        return

    # Deduplicate while preserving order (top_stories before briefing)
    seen: set[str] = set()
    ordered_story_ids: list[str] = []
    for sid in top_story_ids + briefing_story_ids:
        if sid not in seen:
            seen.add(sid)
            ordered_story_ids.append(sid)

    counts: dict[str, int] = {"attempted": 0, "generated": 0, "reused": 0, "failed": 0}

    if not ordered_story_ids:
        _emit_completed(ranking_run_id, ranking_batch_id, counts, trace_id)
        return

    model = _model()

    # ── Phase 1: Read-only ─────────────────────────────────────────────────────
    # Determine which stories need work, load their articles, check content cache.
    # DB session is closed before any LLM call.

    stories_to_generate: list[str] = []
    stories_to_reuse: list[dict[str, Any]] = []
    story_articles: dict[str, list[dict]] = {}
    story_hashes: dict[str, str] = {}

    with SessionLocal() as db:
        repo = StorySummaryRepo(db)
        already_done = repo.get_existing_story_ids(ranking_run_id)
        pending = [sid for sid in ordered_story_ids if sid not in already_done]

        if not pending:
            logger.info(
                "handle_rank_completed run=%s: all %d stories already summarised — nothing to do",
                ranking_run_id, len(already_done),
            )
            _emit_completed(ranking_run_id, ranking_batch_id, counts, trace_id)
            return

        logger.info(
            "handle_rank_completed run=%s: %d total  %d already done  %d to process",
            ranking_run_id, len(ordered_story_ids), len(already_done), len(pending),
        )

        for story_id in pending:
            rows = db.execute(text("""
                SELECT title, content_snippet
                FROM data.normalisation_articles
                WHERE story_id::text = :story_id
                ORDER BY published_at DESC NULLS LAST
                LIMIT 5
            """), {"story_id": story_id}).fetchall()

            articles = [{"title": r[0], "snippet": r[1]} for r in rows]
            if not articles:
                logger.warning(
                    "handle_rank_completed run=%s: no articles for story=%s — skipping",
                    ranking_run_id, story_id,
                )
                counts["failed"] += 1
                continue

            story_articles[story_id] = articles
            content_hash = _compute_content_hash(articles)
            story_hashes[story_id] = content_hash

            cached = repo.get_cached(story_id, content_hash, model)
            if cached:
                logger.info(
                    "handle_rank_completed REUSE    run=%-4s story=%-20s hash=%s  headline=%r",
                    ranking_run_id, story_id, content_hash, cached["headline"][:60],
                )
                stories_to_reuse.append({
                    "story_id": story_id,
                    "ranking_run_id": ranking_run_id,
                    "content_hash": content_hash,
                    "headline": cached["headline"],
                    "summary_text": cached["summary_text"],
                    "why_it_matters": cached["why_it_matters"],
                    "audio_script": cached["audio_script"],
                    "model": model,
                    "summary_version": cached["summary_version"],
                    "confidence": cached["confidence"],
                })
            else:
                stories_to_generate.append(story_id)

    counts["reused"] = len(stories_to_reuse)

    logger.info(
        "handle_rank_completed run=%s: reuse=%d  llm_calls_needed=%d  already_failed=%d",
        ranking_run_id, counts["reused"], len(stories_to_generate), counts["failed"],
    )

    # ── Phase 2: LLM calls ────────────────────────────────────────────────────
    # No DB session held open during network calls.

    generated: list[dict[str, Any]] = []
    for story_id in stories_to_generate:
        articles = story_articles[story_id]
        counts["attempted"] += 1

        result = summarise_story(
            story_id=story_id,
            representative_title=articles[0].get("title") or "",
            articles=articles,
        )

        if result is None:
            logger.warning(
                "handle_rank_completed FAILED   run=%-4s story=%s",
                ranking_run_id, story_id,
            )
            counts["failed"] += 1
            continue

        logger.info(
            "handle_rank_completed GENERATED run=%-4s story=%-20s conf=%.2f  headline=%r",
            ranking_run_id, story_id, result.confidence, result.headline[:60],
        )
        generated.append({
            "story_id": story_id,
            "ranking_run_id": ranking_run_id,
            "content_hash": story_hashes[story_id],
            "headline": result.headline,
            "summary_text": result.summary_text,
            "why_it_matters": result.why_it_matters or None,
            "audio_script": result.audio_script or None,
            "model": model,
            "summary_version": 1,
            "confidence": result.confidence,
        })
        counts["generated"] += 1

    # ── Phase 3: Persist + emit ────────────────────────────────────────────────

    all_rows = stories_to_reuse + generated

    with SessionLocal() as db:
        for row in all_rows:
            db.execute(text("""
                INSERT INTO data.story_summaries (
                    story_id, ranking_run_id, content_hash,
                    headline, summary_text, why_it_matters, audio_script,
                    model, summary_version, confidence
                ) VALUES (
                    :story_id, :ranking_run_id, :content_hash,
                    :headline, :summary_text, :why_it_matters, :audio_script,
                    :model, :summary_version, :confidence
                )
                ON CONFLICT (story_id, ranking_run_id) DO NOTHING
            """), row)

        OutboxRepo(db).add_event(Event(
            type=DATA_SUMMARISE_COMPLETED,
            idempotency_key=f"summarise.completed:{ranking_batch_id}",
            payload={
                "ranking_run_id": ranking_run_id,
                "ranking_batch_id": ranking_batch_id,
                "attempted": counts["attempted"],
                "generated": counts["generated"],
                "reused": counts["reused"],
                "failed": counts["failed"],
            },
            trace_id=trace_id,
        ))

        db.commit()

    logger.info(
        "handle_rank_completed run=%s DONE: attempted=%d generated=%d reused=%d failed=%d",
        ranking_run_id,
        counts["attempted"], counts["generated"], counts["reused"], counts["failed"],
    )


def _emit_completed(
    ranking_run_id: int,
    ranking_batch_id: str,
    counts: dict[str, int],
    trace_id: str | None,
) -> None:
    with SessionLocal() as db:
        OutboxRepo(db).add_event(Event(
            type=DATA_SUMMARISE_COMPLETED,
            idempotency_key=f"summarise.completed:{ranking_batch_id}",
            payload={
                "ranking_run_id": ranking_run_id,
                "ranking_batch_id": ranking_batch_id,
                "attempted": counts["attempted"],
                "generated": counts["generated"],
                "reused": counts["reused"],
                "failed": counts["failed"],
            },
            trace_id=trace_id,
        ))
        db.commit()


def _compute_content_hash(articles: list[dict]) -> str:
    """SHA-256 fingerprint of the top-5 article titles + snippets (first 300 chars each)."""
    parts = []
    for a in articles[:5]:
        title = (a.get("title") or "").strip()
        snippet = (a.get("snippet") or "")[:300].strip()
        parts.append(f"{title}|{snippet}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]
