from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


@dataclass(frozen=True)
class Event:
    """
    Canonical event envelope.

    - idempotency_key: stable key to ensure at-least-once processing doesn't duplicate work
    - type: routing key (e.g. "data.1_ingest.requested")
    - payload: JSON-serializable dict
    """
    type: str
    payload: Mapping[str, Any]
    idempotency_key: str
    trace_id: str | None = None
    event_id: str = ""  # assigned if not provided
    created_at: str = ""  # RFC3339

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d["event_id"]:
            d["event_id"] = str(uuid4())
        if not d["created_at"]:
            d["created_at"] = datetime.now(timezone.utc).isoformat()
        return d