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
from core.pipeline.data.normalise.categorise.cluster_categoriser import categorise_stories
from core.pipeline.data.normalise.categorise.llm_categoriser import DROP

logger = logging.getLogger(__name__)


def _apply_category(db, story_id: int, category: str | None) -> None:
    """Persist the resolved category where it actually matters. Ranking reads the
    representative article's `normalisation_articles.category_primary` (candidate_loader),
    NOT `data.stories.primary_category`, so we set the ARTICLE field for the whole story
    (keeping the representative consistent whichever member is picked) plus the story row
    for consistency. `category=None` DROPS the story (clears the category → excluded)."""
    db.execute(
        text("""
            UPDATE data.normalisation_articles
            SET category_primary = :c,
                category_method  = :m
            WHERE story_id = :id
        """),
        {"c": category, "m": "llm-drop" if category is None else "llm-primary", "id": story_id},
    )
    db.execute(
        text("UPDATE data.stories SET primary_category = :c WHERE id = :id"),
        {"c": category, "id": story_id},
    )


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
        # LLM-primary categorisation, BATCHED: one Anthropic call per ~_BATCH_SIZE stories
        # amortises the taxonomy system prompt (the dominant, uncached input cost). Cached
        # by content hash, so replays/overlaps never re-pay. A whole-batch failure degrades
        # to per-story fallback below (result stays None → majority vote).
        batch_results: dict[int, object] = {}
        if use_llm:
            try:
                batch_results = categorise_stories(db, story_ids)
            except Exception as e:  # never let the LLM path stall the batch
                logger.warning("llm batch categorise failed for %d stories: %s", len(story_ids), e)
                batch_results = {}

        for story_id in story_ids:
            result = batch_results.get(story_id)

            # DELIBERATE DROP: the story has no honest home in our taxonomy (ice hockey,
            # crime, an unlisted region). EXCLUDE it — clear the category, and do NOT fall
            # back to the keyword vote (which would just mis-file it). Correctness > coverage.
            if result is not None and result.primary == DROP:
                _apply_category(db, story_id, None)
                continue

            # KEEP (valid LLM primary) or operational FALLBACK (LLM off/unavailable/failed
            # → legacy majority vote). A DROP never reaches here.
            resolved_category = result.primary if result is not None else _majority_vote(db, story_id)
            if resolved_category is None:
                continue

            _apply_category(db, story_id, resolved_category)

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
