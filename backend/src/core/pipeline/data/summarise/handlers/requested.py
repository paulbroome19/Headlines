from __future__ import annotations

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.summarise.models import SummariseOutput
from core.pipeline.feeds.events import FEEDS_REQUESTED


def handle_summarise_requested(event: dict) -> None:
    """
    Handle: data.summarise.requested

    - Persist summarise output
    - Emit feeds.requested
    """
    payload = event.get("payload") or {}
    trace_id = event.get("trace_id")

    normalisation_article_id = payload.get("normalisation_article_id")
    if normalisation_article_id is None:
        return

    with SessionLocal() as db:
        outbox = OutboxRepo(db)

        # 1) Create summarisation output
        row = SummariseOutput(
            normalisation_article_id=int(normalisation_article_id),
            variant="short",
            content=f"Placeholder summary for normalisation_article_id={normalisation_article_id}",
        )
        db.add(row)
        db.flush()  # ensures row.id is available

        # 2) Trigger feeds pipeline
        outbox.add_event(
            Event(
                type=FEEDS_REQUESTED,
                idempotency_key=f"feeds:{row.id}",
                payload={"summarise_output_ids": [row.id]},
                trace_id=trace_id,
            )
        )

        db.commit()

        