from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.normalise.repos.normalisation_article_repo import NormalisationArticleRepo
from core.pipeline.data.summarise.events import DATA_SUMMARISE_REQUESTED


def handle_normalise_requested(event: dict) -> None:
    payload = event.get("payload") or {}
    trace_id = event.get("trace_id")

    ingestion_run_id = payload.get("ingestion_run_id")
    source = payload.get("source") or "unknown"

    if ingestion_run_id is None:
        return

    with SessionLocal() as db:
        repo = NormalisationArticleRepo(db)
        outbox = OutboxRepo(db)

        # Placeholder normalisation (proves the pipeline)
        row = repo.create(
            ingestion_run_id=int(ingestion_run_id),
            title="Placeholder normalised article",
            url=f"https://example.com/ingestion-run/{ingestion_run_id}",
            source=source,
        )

        outbox.add_event(
            Event(
                type=DATA_SUMMARISE_REQUESTED,
                idempotency_key=f"summarise:{row.id}",
                payload={"normalisation_article_id": row.id},
                trace_id=trace_id,
            )
        )

        db.commit()