from fastapi import APIRouter
from uuid import uuid4

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo
from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED

router = APIRouter(prefix="/data", tags=["data"])


@router.post("/1_ingest/test")
def ingest_test():
    with SessionLocal() as db:
        outbox = OutboxRepo(db)
        outbox.add_event(
            Event(
                type=DATA_INGEST_REQUESTED,
                idempotency_key=f"1_ingest:{uuid4()}",
                payload={"source": "manual-test"},
            )
        )
        db.commit()

    return {"status": "queued"}