from core.platform.db.session import SessionLocal
from core.platform.queue.outbox import OutboxRepo
from core.platform.queue.event import Event

from core.pipeline.data.ingest.repos.ingestion_run_repo import IngestionRunRepo
from core.pipeline.data.normalise.events import DATA_NORMALISE_REQUESTED


def handle_ingest_requested(event: dict) -> None:
    """
    Event: data.ingest.requested

    Responsibilities:
    - create data.ingestion_runs row
    - enqueue data.normalise.requested into event.outbox
    """
    payload = event.get("payload") or {}
    source = payload.get("source") or "unknown"

    # Carry trace_id through if present (optional)
    trace_id = event.get("trace_id")

    with SessionLocal() as db:
        runs = IngestionRunRepo(db)
        outbox = OutboxRepo(db)

        run = runs.create(source=source)

        next_event = Event(
            type=DATA_NORMALISE_REQUESTED,
            idempotency_key=f"normalise:{run.id}",
            payload={
                "ingestion_run_id": run.id,
                "source": source,
            },
            trace_id=trace_id,
        )

        outbox.add_event(next_event)
        db.commit()