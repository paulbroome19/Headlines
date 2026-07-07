# Headlines — Architectural Context

## Pipeline — two halves (NOT one event chain)

The system is two disjoint execution models. Do not assume the whole pipeline is event-driven.

**1. Ingest cascade — event-driven, on a schedule, ends at Rank.**
```
Ingest → Normalise → Cluster → Categorise → Rank
```
Each hop is an event through the outbox → Redis Streams → consumer (see Outbox). The scheduler
fires `data.ingest.requested`; the cascade ends at `ranking/handlers/categorise_completed.py`,
which runs the ranker + editorial precompute + stable ranked-list maintenance **inline** and
emits nothing downstream. (The old `data.rank.completed → summarise → bulletin.assembled` chain
and the whole `feeds→scripts→audio` chain were dead and were deleted — audit §6/§7.)

**2. Generate / play — inline & synchronous inside the HTTP request, NOT event-driven.**
```
Select → Summarise → Assemble → TTS → Playback
```
Lives in `core/api/routes/data.py`: selection picks the stories, `ensure_story_summaries` (lazy,
Haiku) summarises only those, then the assembler + segment-synth build audio. Summarisation and
assembly happen only for the stories a briefing actually selects.

## Core Product Rules

- **Story-level deduplication** — cluster articles into stories; never deduplicate at the article level
- **No duplicate stories in a bulletin** — one story per bulletin slot
- **Diminishing returns across categories** — diversity-aware selection built into the ranker
- **Hybrid warm/on-demand audio** — pre-warm likely stories, generate others on demand

## Selection — one materialised selection, two renderings

The home preview and the briefing MUST show the same stories. Both call
`bulletin/selection.py::resolve_materialised_selection`, which runs one canonical pipeline
(`finalize_selection`: qualify_and_order → drop heard/excluded → `dedup_same_event` → top-block
guard → `cap_bulletin`) over the stable ranked list, materialises the ordered ids per
(profile, day, request_hash) in `data.user_daily_editions`, and pins the day's ranking run. The
preview renders the first N; the briefing assembles them. There is **no live-scoring path** —
the stable `data.ranked_stories` list is the only selection source (the `read_from_ranked_list`
flag was retired, audit §7). Rank-time `data.ranking_runs.top_stories`/`briefing` (from the
legacy `StoryRanker`) is still written and read by the cached-bulletin path + `/ranking` routes.

## Event / Handler / Repo Pattern

Every pipeline stage follows this structure:

```
pipeline/<domain>/
    events.py          # event type constants
    handlers/
        requested.py   # handle_<stage>_requested(event: dict)
    repos/
        <name>_repo.py # SQL via SQLAlchemy text()
    models.py          # SQLAlchemy ORM (currently lightweight; SQL text queries are authoritative)
```

## Outbox Pattern (the ingest half only)

Ingest events are durably published via a Postgres outbox, flushed to Redis Streams; consumers
process exactly-once via a dedup table. NOTE: the outbox + dedup tables live in schema **`event`**
(`event.outbox`, `event.processed`) — NOT `data.processed_events`.

Key files:
- `platform/queue/outbox.py` — `OutboxRepo.add_event()`
- `platform/queue/consumer.py` — Redis Streams consumer; `run_dispatcher.py` flushes the outbox
- `platform/queue/registry_wiring.py` — the **6 live handlers** (`test.event` + ingest / normalise
  / cluster / cluster.completed / categorise.completed). Events with no registered handler are
  acked and dropped. The generate half does NOT use events.

## YAML-First Taxonomy

YAML is pure data. Python is the interpretation layer. **No hardcoded categories in code.**

```
normalise/taxonomy/
    categories.yml   # category tree (hierarchical)
    entities.yml     # flat slug-based entity registry (display_name, entity_type, aliases, categories)
    rules.yml        # keyword + entity rules per category slug
```

`entity_loader.py` reads `entities.yml` — expects flat format with `display_name`, `entity_type`, `aliases`, `categories` per slug. The `category_loader.py` reads `rules.yml`. Both are lazy-loaded singletons.

## Tech Stack

- **FastAPI** — API layer (`core/api/`)
- **SQLAlchemy sync** — all DB access via `text()` queries; ORM models are structural only
- **PostgreSQL** — primary store; schemas: `data` (domain tables), `event` (outbox + processed)
- **Redis Streams** — event bus with consumer groups
- **psycopg binary** driver

## Repo Conventions

- Each pipeline domain owns `models.py`, `events.py`, `handlers/`, `repos/`
- All SQL uses `SessionLocal()` context manager (sync)
- Entities are persisted on-the-fly in the normalise handler; `data.entities` is a lookup/dedup table

## Key Risks to Avoid

- Hardcoded category strings in Python code (use YAML slugs)
- Tight coupling between pipeline stages (communicate only via events)
- Local audio storage (should be remote/hybrid)
- Sync DB pool exhaustion (keep handlers fast; avoid N+1 patterns)

## Session Protocol

At the end of every session, update `DEVLOG.md` with: files changed, decisions made, current state, next step.
