from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, Text, DateTime, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.platform.db.models.base import Base


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = {"schema": "event"}

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)