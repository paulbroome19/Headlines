from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.platform.db.models.outbox_event import OutboxEvent
from core.platform.queue.event import Event


class OutboxRepo:
    def __init__(self, db: Session):
        self.db = db

    def add_event(self, event: Event) -> OutboxEvent:
        data = event.to_dict()
        row = OutboxEvent(
            event_type=data["type"],
            idempotency_key=data["idempotency_key"],
            payload=dict(data),
            trace_id=data.get("trace_id"),
            status="pending",
            attempts=0,
            locked=False,
        )
        self.db.add(row)
        return row

    def fetch_pending(self, limit: int = 50) -> list[OutboxEvent]:
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.status == "pending")
            .where(OutboxEvent.locked.is_(False))
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def mark_published(self, ids: Iterable[str]) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            update(OutboxEvent)
            .where(OutboxEvent.id.in_(list(ids)))
            .values(status="published", published_at=now, locked=False)
        )
        self.db.execute(stmt)

    def mark_failed(self, event_id: str, error: str) -> None:
        stmt = (
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(status="failed", last_error=error, locked=False)
        )
        self.db.execute(stmt)