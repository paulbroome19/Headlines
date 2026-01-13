from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.platform.db.models.processed_event import ProcessedEvent


class ProcessedRepo:
    def __init__(self, db: Session):
        self.db = db

    def is_processed(self, idempotency_key: str) -> bool:
        stmt = select(ProcessedEvent.id).where(ProcessedEvent.idempotency_key == idempotency_key)
        return self.db.execute(stmt).first() is not None

    def mark_processed(self, *, idempotency_key: str, event_type: str) -> None:
        row = ProcessedEvent(idempotency_key=idempotency_key, event_type=event_type)
        self.db.add(row)