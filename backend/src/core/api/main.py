import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.api.router import router
from core.api.middleware.security import SecurityMiddleware
from core.api.worker_registry import register as register_worker

from core.api.routes import audio


logger = logging.getLogger(__name__)


def _start_daemon(name: str, target) -> None:
    """Run a worker's blocking main() loop in a background daemon thread.

    Shape B: the dispatcher and consumer run *inside* the API process rather
    than as separate services. This relies on the API running a SINGLE uvicorn
    worker — multiple workers would each spawn their own dispatcher/consumer
    threads and double-process events. The deploy start command must not pass
    --workers >1 or --reload.
    """
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    register_worker(name, thread)  # so /health can report real liveness
    logger.info("in-process worker thread started: name=%s", name)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Start background workers in-process. The scheduler thread is always
    # started; run_scheduler.main() returns immediately when
    # ENABLE_SCHEDULED_INGEST is false, so a disabled scheduler costs nothing.
    from core.workers.run_dispatcher import main as dispatcher_main
    from core.workers.run_consumer import main as consumer_main
    from core.workers.run_scheduler import main as scheduler_main

    _start_daemon("dispatcher", dispatcher_main)
    _start_daemon("consumer", consumer_main)
    _start_daemon("scheduler", scheduler_main)
    yield
    # Daemon threads are torn down with the process; no explicit join needed.


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
    )
    app = FastAPI(title="Headlines Backend", lifespan=lifespan)
    # API protection (issue #19): app-key gating, dev-endpoint lockdown, and
    # rate limiting — all centralised in one middleware so it applies to every
    # route consistently. Defaults are safe (no enforcement) until env flags are
    # set, so adding this never breaks the running app.
    app.add_middleware(SecurityMiddleware)
    app.include_router(router)
    app.include_router(audio.router)
    return app


app = create_app()
