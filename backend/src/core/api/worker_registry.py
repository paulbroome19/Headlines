"""
Tiny registry of the in-process worker threads the API starts (dispatcher,
consumer, scheduler — see main.py). Exists so /health can report REAL thread
liveness: a silently-dead consumer/scheduler used to be invisible behind a
static {"status": "ok"}.

Kept dependency-free (only threading) so both main.py and the health route can
import it without an import cycle.
"""
from __future__ import annotations

import threading

_threads: dict[str, threading.Thread] = {}


def register(name: str, thread: threading.Thread) -> None:
    """Record a started worker thread under a stable name."""
    _threads[name] = thread


def liveness() -> dict[str, bool]:
    """Map of worker name -> is_alive(). Empty before workers start."""
    return {name: thread.is_alive() for name, thread in _threads.items()}
