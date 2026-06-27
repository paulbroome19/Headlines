"""
Scheduled ingest worker — wall-clock window edition.

Fires data.ingest.requested at aligned wall-clock slots within a UTC window,
then sleeps until the next slot. Restart-safe: never fires immediately on
boot; never catches up missed slots. Enabled only when
ENABLE_SCHEDULED_INGEST=true.
"""
from __future__ import annotations

# --- Schedule (temporary, GNews free-tier sized; retune for paid tier) ---
# 16 fires/day, every INTERVAL_MIN over [START_HOUR, END_HOUR) UTC.
# Last fire of the day: 12:30 UTC.  Next day's first fire: 05:00 UTC.
# Knobs to adjust when moving to a paid GNews tier:
#   SCHEDULE_START_HOUR_UTC  — first slot hour (inclusive)
#   SCHEDULE_END_HOUR_UTC    — cutoff hour (exclusive; last slot = END-INTERVAL)
#   SCHEDULE_INTERVAL_MIN    — minutes between consecutive slots
SCHEDULE_START_HOUR_UTC = 5
SCHEDULE_END_HOUR_UTC   = 13    # exclusive — last fire is 12:30 UTC
SCHEDULE_INTERVAL_MIN   = 30

import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo
from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED

logger = logging.getLogger(__name__)


def next_fire_time(now: datetime) -> datetime:
    """Return the next scheduled fire slot strictly after *now* (UTC-aware).

    Slots are aligned to the wall-clock grid defined by the SCHEDULE_* constants.
    Scans today then tomorrow; tomorrow's first slot is always reachable so the
    function always returns a valid datetime.
    """
    for day_offset in range(2):  # today, then tomorrow
        d = now.date() + timedelta(days=day_offset)
        slot_minutes = SCHEDULE_START_HOUR_UTC * 60
        end_minutes  = SCHEDULE_END_HOUR_UTC   * 60
        while slot_minutes < end_minutes:
            slot = datetime(
                d.year, d.month, d.day,
                slot_minutes // 60, slot_minutes % 60, 0,
                tzinfo=timezone.utc,
            )
            if slot > now:
                return slot
            slot_minutes += SCHEDULE_INTERVAL_MIN
    # Unreachable when START_HOUR < END_HOUR (tomorrow always has a valid first slot)
    raise AssertionError("next_fire_time: no slot found — check SCHEDULE_* constants")


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

    logger.info(
        "scheduler: started  window=%02d:00–%02d:00 UTC  interval=%d min",
        SCHEDULE_START_HOUR_UTC, SCHEDULE_END_HOUR_UTC, SCHEDULE_INTERVAL_MIN,
    )

    while True:
        now  = datetime.now(timezone.utc)
        fire = next_fire_time(now)
        wait = (fire - now).total_seconds()
        logger.info(
            "scheduler: next fire at %s UTC  (sleeping %.0f s)",
            fire.strftime("%H:%M"), wait,
        )
        time.sleep(wait)

        now_utc = datetime.now(timezone.utc)
        logger.info(
            ">>> SCHEDULED INGEST FIRED at %s UTC <<<",
            now_utc.strftime("%H:%M:%S"),
        )

        if _has_pending_ingest():
            logger.info("scheduler: ingest already pending — skipping emit")
        else:
            try:
                _emit_ingest()
            except Exception:
                logger.exception("scheduler: failed to emit ingest event")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    main()
