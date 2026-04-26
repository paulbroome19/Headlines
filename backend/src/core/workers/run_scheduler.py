"""
Scheduled ingest worker.

Emits data.ingest.requested into the outbox at a fixed interval.
Enabled only when ENABLE_SCHEDULED_INGEST=true.
Skips the tick if a pending ingest event is already in the outbox
(prevents overlap when the pipeline is still running).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo
from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED

logger = logging.getLogger(__name__)


def _has_pending_ingest() -> bool:
    with SessionLocal() as db:
        n = db.execute(text("""
            SELECT COUNT(*) FROM event.outbox
            WHERE event_type = :t AND status = 'pending'
        """), {"t": DATA_INGEST_REQUESTED}).scalar()
        return (n or 0) > 0


def _emit_ingest() -> None:
    key = f"ingest:scheduled:{uuid4()}"
    with SessionLocal() as db:
        OutboxRepo(db).add_event(Event(
            type=DATA_INGEST_REQUESTED,
            idempotency_key=key,
            payload={"source": "scheduler"},
        ))
        db.commit()
    logger.info("scheduler: emitted  event=%s  key=%s", DATA_INGEST_REQUESTED, key)


def main() -> None:
    if not settings.enable_scheduled_ingest:
        logger.info("scheduler: ENABLE_SCHEDULED_INGEST not set — exiting")
        return

    interval_min = settings.ingest_interval_minutes
    logger.info("scheduler: started  interval=%d min", interval_min)

    while True:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        logger.info("scheduler: tick  time=%s", now)

        if _has_pending_ingest():
            logger.info("scheduler: ingest already pending — skipping tick")
        else:
            try:
                _emit_ingest()
            except Exception:
                logger.exception("scheduler: failed to emit ingest event")

        logger.info("scheduler: sleeping %d min", interval_min)
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    main()
