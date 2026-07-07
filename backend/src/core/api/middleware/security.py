"""
API protection middleware (issue #19) — minimal but solid, for ~20 users + a
cold demo judged by a technical CTO. NOT an identity system; there is no per-user
auth or login. Three concerns, all centralised here so they apply consistently to
every route without touching endpoint/generation code:

  PART 1  App key   — when settings.require_api_key, requests to /data/* and
                      /feeds/* must send `X-API-Key: <API_ACCESS_KEY>`, else 401.
                      OFF by default so deploying never breaks the running app.
  PART 2  Dev gate  — when NOT settings.enable_dev_endpoints, dev/inspection
                      endpoints (/dev/api/* except audio, /data/ingest/test,
                      /data/*/latest dev reads, /scripts/*) return 404.
  PART 3  Rate limit— expensive LLM/TTS endpoints (manifest, bulletin, assemble,
                      audio, ingest) are capped per-IP as a cost/DoS backstop.

Exemptions (always reachable): /health and audio DELIVERY (/data/segments/*,
/audio/outputs/*, /dev/api/audio/*) — no paid work, no internals, and keeping
them key-free avoids media-player header friction in the app.

Settings are read live per request; Railway restarts the process on env change,
so a flag flip takes effect on redeploy.
"""
from __future__ import annotations

import hmac
import logging
import re
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.platform.config.settings import settings

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


# ── Path classification ──────────────────────────────────────────────────────

def _is_audio_delivery(path: str) -> bool:
    """Cached-audio delivery — no paid work, no internals. Never key-gated."""
    return (
        path.startswith("/data/segments")
        or path.startswith("/audio/outputs")
        or path.startswith("/dev/api/audio/")
    )


def _is_key_gated(path: str) -> bool:
    """App-facing data/content endpoints protected by the shared app key."""
    if _is_audio_delivery(path):
        return False
    return path.startswith("/data") or path.startswith("/feeds")


# Dev/inspection endpoints disabled in production. The app's audio download
# /dev/api/audio/{id}/file is deliberately excluded — it stays reachable.
_DEV_ONLY_EXACT = {
    "/dev",
    "/dev/",
    "/data/ingest/test",
    "/data/ranking/latest",
    "/data/summaries/latest",
    "/data/bulletins/latest",
}


def _is_dev_only(path: str) -> bool:
    if path in _DEV_ONLY_EXACT:
        return True
    if path.startswith("/dev/api/") and not path.startswith("/dev/api/audio/"):
        return True
    if path == "/scripts" or path.startswith("/scripts/"):
        return True
    return False


# Expensive endpoints that trigger LLM/TTS — the ones worth a cost backstop.
_EXPENSIVE_PATTERNS = (
    re.compile(r"^/data/profiles/\d+/manifest$"),
    re.compile(r"^/data/profiles/\d+/bulletin$"),
    re.compile(r"^/data/bulletins/assemble$"),
    re.compile(r"^/data/bulletins/assemble-and-audio$"),
    re.compile(r"^/data/bulletins/\d+/audio$"),
    re.compile(r"^/data/ingest/test$"),
)


def _is_expensive(method: str, path: str) -> bool:
    return method == "POST" and any(p.match(path) for p in _EXPENSIVE_PATTERNS)


def _client_ip(request: Request) -> str:
    """First hop of X-Forwarded-For (Railway sits behind a proxy), else peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── In-memory fixed-window rate limiter ──────────────────────────────────────

class _RateLimiter:
    """
    Per-key fixed-window counter. In-memory is correct here: the API runs a
    single uvicorn worker (Shape B), so there's one shared process. Thread-safe
    because the consumer/dispatcher daemons share the process.
    """

    def __init__(self) -> None:
        self._hits: dict[str, list] = {}  # key -> [window_start, count]
        self._lock = threading.Lock()

    def check(self, key: str, max_hits: int, window: float) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            entry = self._hits.get(key)
            if entry is None or now - entry[0] >= window:
                entry = [now, 0]
            entry[1] += 1
            self._hits[key] = entry
            allowed = entry[1] <= max_hits
            retry_after = max(1, int(window - (now - entry[0]))) if not allowed else 0
            return allowed, retry_after


# ── Middleware ───────────────────────────────────────────────────────────────

class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._limiter = _RateLimiter()

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        method = request.method

        # PART 2 — dev gate (404 before anything else; don't reveal existence).
        if _is_dev_only(path) and not settings.enable_dev_endpoints:
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # PART 1 — shared app key on gated paths.
        #
        # ┌─────────────────────────────────────────────────────────────────────────────┐
        # │ TODO(2026-07-07): APP-KEY ENFORCEMENT IS OFF IN PROD.                        │
        # │ REQUIRE_API_KEY is unset → settings.require_api_key=False, so /data/* and    │
        # │ /feeds/* are served with NO app-key check. This is a DELIBERATE dark-ship:   │
        # │ the server shipped before the iOS app learned to send the key, so turning it │
        # │ on now would 401 every existing client. To flip it ON, in THIS ORDER:        │
        # │   1. Generate a strong key:  openssl rand -hex 32                            │
        # │   2. Ship an iOS build that sends it as the `X-API-Key` header on every      │
        # │      /data/* and /feeds/* request, and wait until that build is live for     │
        # │      ~all users (staged rollout complete) — otherwise old clients 401.       │
        # │   3. THEN set BOTH on the Headlines service together:                        │
        # │        API_ACCESS_KEY=<key>   AND   REQUIRE_API_KEY=true                     │
        # │      (both at once — the guard below fails CLOSED with 503 if the flag is on │
        # │      while API_ACCESS_KEY is empty).                                         │
        # │   Rollback = set REQUIRE_API_KEY=false (no redeploy of the app needed).      │
        # │   See the API-protection-rollout memo for the full flip/rollback runbook.    │
        # └─────────────────────────────────────────────────────────────────────────────┘
        if settings.require_api_key and _is_key_gated(path):
            server_key = (settings.api_access_key or "").strip()
            if not server_key:
                # Operator turned enforcement on but forgot the key. Fail closed
                # and loudly rather than silently leave the API open.
                logger.critical(
                    "REQUIRE_API_KEY is on but API_ACCESS_KEY is unset — denying gated requests"
                )
                return JSONResponse(
                    {"detail": "Server auth misconfigured"}, status_code=503
                )
            client_key = request.headers.get(API_KEY_HEADER, "")
            if not hmac.compare_digest(client_key, server_key):
                return JSONResponse(
                    {"detail": "Invalid or missing API key"}, status_code=401
                )

        # PART 3 — rate limit expensive endpoints (per IP), independent of auth.
        if _is_expensive(method, path):
            allowed, retry_after = self._limiter.check(
                _client_ip(request),
                settings.rate_limit_max,
                settings.rate_limit_window_seconds,
            )
            if not allowed:
                logger.warning(
                    "rate limit hit: ip=%s %s %s", _client_ip(request), method, path
                )
                return JSONResponse(
                    {"detail": "Rate limit exceeded — slow down"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

        return await call_next(request)
