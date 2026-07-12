# Worker split — move the ingest cascade out of the web process

**Status:** approved (Tuesday's first job). Fixes the single-process starvation (audit gap #16).
**Not** the fix for the cold-play hang — that was the client `%3F` readiness-URL 404 (PR #206),
proven separate. This is the latency/scale root cause: one process serves HTTP + runs the ingest
cascade (248 ranking runs/day + editorial/summarise/categorise LLM) as daemon threads; live
readiness measured 3.65 s under that contention.

## 1. What moves, what stays
- **Move to a worker service:** `scheduler` (fires ingest pools), `dispatcher` (outbox→Redis), and
  `consumer` (Redis→handlers) — i.e. the **entire ingest cascade** ingest→normalise→cluster→
  categorise→rank+editorial+ranked-list (all runs inside consumer handlers). This is the continuous
  throughput load doing the starving.
- **Stays in web:** all FastAPI HTTP + the **entire generate path** (select→summarise→connective→
  assemble→TTS→readiness), **including `run_background_assembly`**.
- **Generate-path assembly stays in web, deliberately:** it is **user-latency-bound** (a person is
  watching *their* loader), **bursty and request-scoped** (fires from the manifest POST, scales with
  active plays), and needs the request context. Ingest is **throughput-bound and continuous**. The
  starvation is these opposite workloads sharing one process/GIL/pool/threadpool. Moving ingest out
  removes the continuous load; keeping generate in web keeps user latency decoupled from ingest
  queue depth and lets the web service scale with users.

## 2. Shared state — already cross-process-safe? Yes, two caveats
- Outbox (`event.outbox`, Postgres) ✅ · Redis Streams + consumer group ✅ (built for this) · DB ✅.
- **In-process assumptions that break — exactly two:**
  1. `api/main.py` lifespan spawns the three workers as **daemon threads** in the web process. Post-
     split the web must NOT spawn them; the worker runs them as its main. The **scheduler** must run
     in exactly one place or it double-fires.
  2. The **in-memory rate limiter** (`security.py`) assumes one process — not broken by this split
     (web stays 1 instance) but it is the blocker for later web horizontal scaling. Flag, don't fix.
- Two processes = 2× the 30-conn pool = 60 connections → verify Railway Postgres `max_connections`.
- No shared *memory* between ingest and generate (they communicate via DB rows) → nothing else breaks.

## 3. Deploy shape (zero client change, zero schema change — confirmed)
- **Second Railway service, same repo.** Web: `uvicorn core.api.main:app`, env `RUN_WORKERS=false`.
  Worker: new `python -m core.workers.run_all` supervisor (runs the three `run_*` mains with the
  restart-with-backoff the audit flagged as missing), env `RUN_WEB=false` (no HTTP surface).
- **The gate is one env flag** in `main.py` lifespan: `if settings.run_workers:` around the
  thread-spawn (default true → backward-compatible; false on web).
- **Migrations on the web service only** (`preDeployCommand: alembic upgrade head`) — not both, or
  they race.
- **Zero client change:** the client talks only to the web HTTP service; the worker has no HTTP
  surface. **Zero schema change:** outbox/`event.processed`/Redis all exist; no new tables.
  Exactly-once still holds (the worker runs ONE consumer, same as today; multi-consumer would need
  the filed `ON CONFLICT` upsert — a later step).

## Sizing
Medium. New supervisor entrypoint + the `RUN_WORKERS` gate + a second Railway service (start
command, shared env, migration-on-one) + verification that the web stopped running workers and the
worker runs them without double-scheduling. A few careful hours + a deploy dance. Do it unhurried —
a rushed split risks double-ingest or a silently-dead worker (the §9 supervision gap).

## Standing design smell — day-keyed cache identity rolls at 23:00 UTC (fold into the split's review)
`request_hash` (selector.py) folds `day = uk_now().date()` into the cache key, and the UK date rolls
at **00:00 BST = 23:00 UTC** — mid-evening, while people are actively using the app. Every artifact
keyed on that hash (materialised editions in `data.user_daily_editions`, the bulletin cache) gets a
NEW identity at 23:00 UTC, so any flow that straddles that instant sees two different keys for what
the user experiences as one session. That is exactly what produced the home≠briefing order divergence
(home preview computes the hash on day D, the manifest 90s later on day D+1 → different edition). The
L-D full-pin fix (PR: honor `run:hash`, refresh loudly on a missing pinned edition) handles THIS edge,
but the root shape — a cache identity that rolls at a high-traffic hour — will keep manufacturing
edge cases (a briefing assembled at 22:59 cached under D, re-requested at 23:01 recomputes D+1 and
misses; warm-up done pre-roll invalidated post-roll; readiness pinned to a D edition while the manifest
moved to D+1). Options to weigh during the split (do NOT silently change the key — it invalidates every
cached bulletin): (a) roll the "briefing day" at a genuine low-traffic local hour (e.g. 03:00–04:00
UK) instead of midnight; (b) make the day boundary explicit state carried on the selection identity
rather than recomputed independently at each hop; (c) accept it and rely on L-D's loud-refresh as the
single sanctioned path across the boundary. Whichever — the boundary must fail LOUD (refresh + client
re-sync), never silently serve a stale-day edition. Track alongside the in-memory rate-limiter as a
"single-process / single-clock assumption" to revisit when the topology changes.

## Prerequisite hygiene (fold in)
Give the generate-path background assembly its **own** ThreadPoolExecutor, separate from FastAPI's
request-serving threadpool, so an assembly burst can't saturate the pool serving HTTP (incl.
readiness). Note: the routes are sync (`def`) so they already run off the event loop, and blocking
I/O (psycopg2, ElevenLabs, Anthropic) releases the GIL — so "offload to save the loop" is largely a
misframe; the real contention is threadpool + DB-pool saturation + GIL under LLM volume, which the
split (moving ingest out) is what actually fixes.
