from __future__ import annotations

import logging
import uuid
from collections import Counter

from sqlalchemy import text

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.categorise.events import DATA_CATEGORISE_COMPLETED
from core.pipeline.data.normalise.categorise.cluster_categoriser import categorise_story

logger = logging.getLogger(__name__)


def _majority_vote(db, story_id: int) -> str | None:
    """Legacy fallback: the most common article-level primary across the story. Used only
    when the LLM path is disabled or unavailable — kept so the pipeline never stalls."""
    rows = db.execute(
        text("""
            SELECT category_primary FROM data.normalisation_articles
            WHERE story_id = :story_id AND category_primary IS NOT NULL
        """),
        {"story_id": story_id},
    ).scalars().all()
    if not rows:
        return None
    return Counter(rows).most_common(1)[0][0]


def handle_cluster_completed(event: dict) -> None:
    """
    Triggered by: data.cluster.completed

    Responsibilities:
    - For each story, resolve primary_category LLM-PRIMARY at the CLUSTER level: one
      Haiku call per story, constrained to valid taxonomy leaves, pool topic/country as
      hints only, cached by content hash (see cluster_categoriser). This replaces the
      context-blind keyword majority vote that mis-slugged stories (e.g. "shares" verb →
      business.markets). The keyword majority survives only as a fallback when the LLM
      path is off/unavailable.
    - Also record the LLM's secondary labels on the primary article (multi-label).
    - Update data.stories.primary_category; emit data.categorise.completed.
    """
    payload = event.get("payload") or {}
    story_ids: list[int] = payload.get("story_ids") or []
    cluster_batch_id = payload.get("cluster_batch_id")
    ingestion_run_id = payload.get("ingestion_run_id")
    trace_id = event.get("trace_id")

    if not story_ids:
        return

    use_llm = settings.enable_llm_primary_categorise
    with SessionLocal() as db:
        for story_id in story_ids:
            resolved_category: str | None = None

            if use_llm:
                try:
                    result = categorise_story(db, story_id)
                except Exception as e:  # never let one story stall the batch
                    logger.warning("llm categorise failed for story %s: %s", story_id, e)
                    result = None
                if result is not None:
                    resolved_category = result.primary

            if resolved_category is None:
                # LLM off / unavailable / failed → keep the legacy majority vote.
                resolved_category = _majority_vote(db, story_id)

            if resolved_category is None:
                continue

            db.execute(
                text(
                    """
                    UPDATE data.stories
                    SET primary_category = :category
                    WHERE id = :story_id
                      AND (primary_category IS DISTINCT FROM :category)
                    """
                ),
                {"category": resolved_category, "story_id": story_id},
            )

        categorisation_batch_id = str(uuid.uuid4())

        outbox = OutboxRepo(db)
        outbox.add_event(Event(
            type=DATA_CATEGORISE_COMPLETED,
            idempotency_key=f"categorise.completed:{cluster_batch_id}",
            payload={
                "story_ids": story_ids,
                "cluster_batch_id": cluster_batch_id,
                "categorisation_batch_id": categorisation_batch_id,
                "ingestion_run_id": ingestion_run_id,
            },
            trace_id=trace_id,
        ))

        db.commit()
