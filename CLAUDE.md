# Headlines — Architectural Context

## Pipeline Stages (in order)

```
Ingest → Normalise → Cluster → Categorise → Rank → Select → Summarise → TTS → Playback
```

The current implementation covers: **Ingest → Normalise → Cluster**, with Ranking as a standalone module.

## Core Product Rules

- **Story-level deduplication** — cluster articles into stories; never deduplicate at the article level
- **No duplicate stories in a bulletin** — one story per bulletin slot
- **Diminishing returns across categories** — diversity-aware selection built into the ranker
- **Hybrid warm/on-demand audio** — pre-warm likely stories, generate others on demand

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

## Outbox Pattern

Events are durably published via a PostgreSQL outbox table, flushed to Redis Streams. Consumer groups process events exactly-once using a `data.processed_events` deduplication table.

Key files:
- `platform/queue/outbox.py` — `OutboxRepo.add_event()`
- `platform/queue/consumer.py` — Redis Streams consumer
- `platform/queue/registry_wiring.py` — all handlers registered here

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
- **PostgreSQL** — primary store; schemas: `data`, `feeds`, `scripts`
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

At the end of every session, update `PROGRESS.md` with: files changed, decisions made, current state, next step.
