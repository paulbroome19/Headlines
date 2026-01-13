from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.api.di import get_db
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

router = APIRouter()

@router.get("/health")
def health() -> dict:
    return {"status": "ok"}

@router.post("/health/test-event")
def test_event(db: Session = Depends(get_db)) -> dict:
    repo = OutboxRepo(db)
    e = Event(
        type="test.event",
        idempotency_key="test.event:1",
        payload={"hello": "world"},
    )
    row = repo.add_event(e)
    db.commit()
    return {"created_outbox_event_id": str(row.id)}