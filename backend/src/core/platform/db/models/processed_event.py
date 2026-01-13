from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.platform.db.models.base import Base


class ProcessedEvent(Base):
    __tablename__ = "processed"
    __table_args__ = {"schema": "event"}

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)