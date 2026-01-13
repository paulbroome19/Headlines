from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.feeds.events import FEEDS_BUILT
from core.pipeline.feeds.repos.feed_repo import FeedRepo
from core.pipeline.feeds.repos.feed_item_repo import FeedItemRepo
from core.pipeline.scripts.events import SCRIPTS_REQUESTED


def handle_feeds_requested(event: dict) -> None:
    """
    Handle: feeds.requested

    Minimal v1 behaviour:
    - Create a feeds.feeds row
    - Create feeds.feed_items rows referencing data.summarise_outputs via summarise_output_id
    - Emit feeds.built and scripts.requested
    """
    payload = event.get("payload") or {}
    trace_id = event.get("trace_id")

    summarise_output_ids = payload.get("summarise_output_ids") or []
    if not isinstance(summarise_output_ids, list) or not summarise_output_ids:
        return

    with SessionLocal() as db:
        feeds = FeedRepo(db)
        items = FeedItemRepo(db)
        outbox = OutboxRepo(db)

        feed = feeds.create(source="pipeline")

        for idx, oid in enumerate(summarise_output_ids):
            items.add(
                feed_id=feed.id,
                summarise_output_id=int(oid),
                position=idx + 1,
            )

        outbox.add_event(
            Event(
                type=FEEDS_BUILT,
                idempotency_key=f"feeds:built:{feed.id}",
                payload={"feed_id": feed.id},
                trace_id=trace_id,
            )
        )

        outbox.add_event(
            Event(
                type=SCRIPTS_REQUESTED,
                idempotency_key=f"scripts:{feed.id}",
                payload={"feed_id": feed.id},
                trace_id=trace_id,
            )
        )

        db.commit()