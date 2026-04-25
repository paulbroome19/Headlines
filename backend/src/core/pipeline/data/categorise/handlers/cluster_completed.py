from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.categorise.events import DATA_CATEGORISE_COMPLETED


def handle_cluster_completed(event: dict) -> None:
    """
    Triggered by: data.cluster.completed

    Responsibilities:
    - For each story in story_ids, load all constituent articles
    - Resolve primary_category by majority vote across articles (more robust
      than the representative-article assignment done during clustering)
    - Update data.stories.primary_category where the vote differs
    - Emit data.categorise.completed
    """
    payload = event.get("payload") or {}
    story_ids: list[int] = payload.get("story_ids") or []
    cluster_batch_id = payload.get("cluster_batch_id")
    ingestion_run_id = payload.get("ingestion_run_id")
    trace_id = event.get("trace_id")

    if not story_ids:
        return

    with SessionLocal() as db:
        for story_id in story_ids:
            # Load category_primary for every article in this story.
            # normalisation_articles.story_id is set by the cluster handler.
            rows = db.execute(
                text(
                    """
                    SELECT category_primary
                    FROM data.normalisation_articles
                    WHERE story_id = :story_id
                      AND category_primary IS NOT NULL
                    """
                ),
                {"story_id": story_id},
            ).scalars().all()

            if not rows:
                continue

            # Majority vote: pick the most common category across all articles.
            counts: Counter[str] = Counter(rows)
            resolved_category = counts.most_common(1)[0][0]

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
