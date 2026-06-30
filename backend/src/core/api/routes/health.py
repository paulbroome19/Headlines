from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.api.di import get_db
from core.api.worker_registry import liveness
from core.platform.config.settings import settings
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

router = APIRouter()

# Workers that run a forever-loop and must always be alive in a healthy process.
# The scheduler is handled separately: it exits cleanly when scheduled ingest is
# disabled, so a dead scheduler is only a problem when it's meant to be running.
_ALWAYS_ON_WORKERS = ("consumer", "dispatcher")


@router.get("/health")
def health() -> dict:
    """
    Liveness-aware health check. Reports each in-process worker thread's real
    state so a silently-dead consumer/scheduler becomes visible (it used to hide
    behind a static "ok"). Lightweight: just reads Thread.is_alive().

    status is "degraded" if any worker that SHOULD be running is dead, else "ok".
    Still returns HTTP 200 either way so the platform health probe stays stable;
    the body carries the real signal.
    """
    alive = liveness()
    workers: dict[str, str] = {}
    degraded: list[str] = []

    for name in _ALWAYS_ON_WORKERS:
        if name not in alive:
            continue  # workers not started yet (e.g. before lifespan) — don't flag
        if alive[name]:
            workers[name] = "alive"
        else:
            workers[name] = "dead"
            degraded.append(name)

    if "scheduler" in alive:
        if not settings.enable_scheduled_ingest:
            workers["scheduler"] = "disabled"
        elif alive["scheduler"]:
            workers["scheduler"] = "alive"
        else:
            workers["scheduler"] = "dead"
            degraded.append("scheduler")

    result: dict = {"status": "degraded" if degraded else "ok", "workers": workers}
    if degraded:
        result["degraded"] = degraded
    return result

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