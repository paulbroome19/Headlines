"""
Scheduled ingest worker — tiered pool edition (docs/ingestion-pool-design.md Part 3).

Each POOL = one (category, country) GNews request, fired on its own aligned
wall-clock grid:
  Tier 1   — core finance-commuter coverage, every 60 min
  Tier 1.5 — the rest of the GB topic categories, every 3 h
  Tier 2   — major economies / Commonwealth, 3×/day (every 8 h)
  Tier 3   — the long tail, 1×/day
Priorities (business / politics-via-nation / top-stories) are the frequent tiers.

Restart-safe: slots are aligned to the wall-clock grid from midnight UTC, so a
restart never double-fires a slot and never catches up missed slots. Enabled only
when ENABLE_SCHEDULED_INGEST=true.

Budget (hard constraint < 1000 req/day) is computed from the pool intervals at
import and asserted — see daily_request_budget().
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo
from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED

logger = logging.getLogger(__name__)

# Intervals (minutes) → fires/day = 1440 / interval.
_T1, _T15, _T2, _T3 = 60, 180, 480, 1440


@dataclass(frozen=True)
class Pool:
    category: str          # coarse stamp label: general/business/nation/world/technology/sports/science/health/entertainment
    country: str           # 2-letter GNews country
    interval_min: int      # aligned wall-clock cadence

    @property
    def fires_per_day(self) -> int:
        return 1440 // self.interval_min


def _build_pools() -> list[Pool]:
    pools: list[Pool] = []

    # TIER 1 — core (every 60 min): general/business/nation for gb + us.
    for country in ("gb", "us"):
        for cat in ("general", "business", "nation"):
            pools.append(Pool(cat, country, _T1))

    # TIER 1.5 — UK comprehensiveness (every 3 h): remaining GB topic categories.
    for cat in ("world", "technology", "sports", "science", "health", "entertainment"):
        pools.append(Pool(cat, "gb", _T15))

    # TIER 2 — major economies / Commonwealth (3×/day).
    t2_headlines = ("de", "fr", "it", "es", "nl", "ie", "ca", "in", "cn", "jp",
                    "kr", "au", "za", "ng", "br", "ae", "il", "ua")
    t2_business = ("de", "fr", "cn", "jp", "in", "ca", "au", "hk", "sg", "ae")
    for c in t2_headlines:
        pools.append(Pool("general", c, _T2))
    for c in t2_business:
        pools.append(Pool("business", c, _T2))

    # TIER 3 — long tail (1×/day, general headlines only).
    t3 = ("ar", "at", "bd", "be", "bw", "bg", "cl", "co", "cu", "cz", "eg", "ee",
          "et", "fi", "gh", "gr", "hu", "id", "ke", "lv", "lb", "lt", "my", "mx",
          "ma", "na", "no", "pk", "pe", "ph", "pl", "pt", "ro", "ru", "sa", "sn",
          "sk", "si", "se", "ch", "tw", "tz", "th", "tr", "ug", "ve", "vn", "zw")
    for c in t3:
        pools.append(Pool("general", c, _T3))

    return pools


POOLS: list[Pool] = _build_pools()


def daily_request_budget() -> int:
    """Total GNews requests/day across all pools. Hard constraint: < 1000."""
    return sum(p.fires_per_day for p in POOLS)


# Fail fast at import if the schedule ever exceeds the paid-tier budget.
assert daily_request_budget() < 1000, (
    f"pool schedule exceeds 1000 req/day: {daily_request_budget()}"
)


def _next_slot(now: datetime, interval_min: int) -> datetime:
    """Next wall-clock slot strictly after *now*, aligned to the interval grid
    from midnight UTC. Rolls into the next day cleanly for daily pools."""
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    mins_since = (now - day_start).total_seconds() / 60.0
    next_mult = (int(mins_since // interval_min) + 1) * interval_min
    return day_start + timedelta(minutes=next_mult)


def _emit_pool(pool: Pool, slot: datetime) -> None:
    # Idempotency key per (pool, slot) → a restart within the same slot can't
    # double-process (consumer dedups on the key).
    key = f"ingest:scheduled:{pool.category}:{pool.country}:{slot.strftime('%Y%m%dT%H%M')}"
    with SessionLocal() as db:
        OutboxRepo(db).add_event(Event(
            type=DATA_INGEST_REQUESTED,
            idempotency_key=key,
            payload={
                "source": "scheduler",
                "provider": "gnews",
                "category": pool.category,
                "country": pool.country,
            },
        ))
        db.commit()


def _utc_midnight(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _scheduler_requests_today(now: datetime) -> int:
    """Restart-safe count of scheduler-emitted GNews requests since UTC midnight,
    read from the authoritative data.ingestion_runs rows. Seeds the burst budget
    counter so a mid-day restart can't re-burst past the daily hard cap."""
    try:
        with SessionLocal() as db:
            n = db.execute(
                text(
                    "SELECT count(*) FROM data.ingestion_runs "
                    "WHERE source = 'scheduler' AND created_at >= :m"
                ),
                {"m": _utc_midnight(now)},
            ).scalar()
        return int(n or 0)
    except Exception:
        logger.exception("scheduler: failed to read ingestion_runs count — assuming 0")
        return 0


def _run_burst(burst_day) -> None:
    """One-day aggressive fill: fire the full pool grid in fast rotation until the
    daily hard cap is spent, then idle until UTC midnight. One-shot — only the UTC
    day the burst was enabled; after midnight main() falls back to the normal tiers.

    The budget counter is seeded from the DB (restart-safe) and incremented locally
    per emit, so it never blows past ingest_daily_hard_cap even across restarts."""
    cap = settings.ingest_daily_hard_cap
    batch = max(1, settings.ingest_burst_batch)
    interval = max(1, settings.ingest_burst_interval_sec)
    logger.warning(
        "scheduler: BURST MODE active for %s UTC — full grid (%d pools), %d/tick every %ds, hard cap %d req/day",
        burst_day, len(POOLS), batch, interval, cap,
    )

    burst_idx = 0
    emitted = _scheduler_requests_today(datetime.now(timezone.utc))

    while True:
        now = datetime.now(timezone.utc)

        # One-shot: once the calendar day rolls over, stop bursting and hand back
        # to the normal tiered scheduler (even if the flag is still set).
        if now.date() != burst_day:
            logger.warning("scheduler: burst day %s elapsed — reverting to normal tiered schedule", burst_day)
            return

        if emitted >= cap:
            nxt = _utc_midnight(now) + timedelta(days=1)
            secs = (nxt - now).total_seconds()
            logger.warning(
                "scheduler: burst cap reached (%d/%d today) — idling %.1fh until %s UTC, then normal tiers",
                emitted, cap, secs / 3600.0, nxt.strftime("%Y-%m-%d %H:%M"),
            )
            time.sleep(min(secs, 900))   # cap the sleep so a restart/rollover re-checks promptly
            continue

        # Fire a batch across the full grid, never exceeding the remaining budget.
        slot = now.replace(second=0, microsecond=0)
        n = min(batch, cap - emitted)
        for _ in range(n):
            pool = POOLS[burst_idx % len(POOLS)]
            burst_idx += 1
            try:
                _emit_pool(pool, slot)   # minute-stamped key; distinct pools ⇒ distinct keys
                emitted += 1
            except Exception:
                logger.exception("scheduler: burst emit failed for %s/%s", pool.category, pool.country)
        logger.info("scheduler: burst fired %d pools  (%d/%d today)", n, emitted, cap)
        time.sleep(interval)


def main() -> None:
    if not settings.enable_scheduled_ingest:
        logger.info("scheduler: ENABLE_SCHEDULED_INGEST not set — exiting")
        return

    logger.info(
        "scheduler: started  pools=%d  budget=%d req/day (< 1000)",
        len(POOLS), daily_request_budget(),
    )

    # One-day burst override: run the aggressive fill for today, then fall through
    # to the normal tiered loop below (which resumes at the next UTC midnight).
    if settings.ingest_burst_mode:
        _run_burst(datetime.now(timezone.utc).date())

    while True:
        now = datetime.now(timezone.utc)

        # Group pools by their next slot; the earliest slot is the next wake-up.
        by_slot: dict[datetime, list[Pool]] = {}
        for p in POOLS:
            by_slot.setdefault(_next_slot(now, p.interval_min), []).append(p)

        fire_at = min(by_slot)
        due = by_slot[fire_at]
        wait = (fire_at - now).total_seconds()
        logger.info(
            "scheduler: next fire at %s UTC — %d pools  (sleeping %.0f s)",
            fire_at.strftime("%H:%M"), len(due), wait,
        )
        if wait > 0:
            time.sleep(wait)

        logger.info(">>> SCHEDULED POOL FIRE at %s UTC — %d pools <<<",
                    fire_at.strftime("%H:%M"), len(due))
        for p in due:
            try:
                _emit_pool(p, fire_at)
            except Exception:
                logger.exception("scheduler: failed to emit pool %s/%s", p.category, p.country)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
