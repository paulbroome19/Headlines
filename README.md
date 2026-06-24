# Headlines

A mobile-first, audio-based news app that delivers personalised,
deduplicated bulletins as a single seamless listen — think "instant
radio, but only the stories you haven't heard yet."

Built as a personal project to explore audio-first news consumption,
event-driven backend architecture, and cost-efficient AI infrastructure.

## What it does

You open the app, press play, and hear a short curated audio briefing
made from the day's news. Each bulletin:

- Pulls fresh stories from external news sources
- Clusters articles into single canonical stories (so multiple
  outlets reporting the same event = one story, not five)
- Selects a balanced set of stories based on your category preferences
- Generates a continuous audio bulletin: intro greeting → stories
  with transitions → outro
- Tracks what you've heard so you never get the same story twice,
  across any future bulletin

The player itself is built around a chaptered-podcast model:
continuous scrubber, skip controls, per-story status indicators
(playing / listened-to / not played), and a music-player-style
"now playing" headline.

## What's interesting about it

A few architectural decisions worth flagging:

**Story-level deduplication.** Dedup happens at the canonical story
level, not the article level. Multiple articles from different
outlets cluster into a single `story_hash`. This is the foundation
the rest of the system is built on.

**Content-addressed audio cache.** TTS audio is keyed by a hash of
the segment text, voice, model, and format. One generation per
unique segment, ever — shared across every user that hears it.
This decoupled TTS cost from user count: a bulletin played by 1,000
users costs the same to generate as a bulletin played by 1.

**On-demand generation.** For a self-funded single-user app, AI and
TTS only fire when a bulletin is actually requested. Story ingestion
runs free; the paid work (summarisation, audio generation) is
triggered by user action and cached forever once generated.

**Listened-to state machine.** Every story is tracked per user as
queued / consumed / rejected, with rules for forward-scrubbing,
backward replay, and abandonment. The "never the same story twice"
guarantee is enforced at bulletin assembly time, not patched in
after.

**Streaming audio with intro masking.** iOS uses AVQueuePlayer with
sliding-window pre-fetch. The sting + intro provide cover while
later segments load, so the user never hears a loading gap.

## Architecture

Ingestion → Normalisation → Story Clustering → Categorisation → Storage
↓
Bulletin Assembly (on demand)
↓
Summarisation + TTS generation
↓
Segment manifest → iOS player

- **Backend**: Python, FastAPI, Postgres. Event-driven pipeline,
  modular ingestion handlers, async workers.
- **iOS**: Swift / SwiftUI, AVQueuePlayer-based player with
  custom seek model, listened-to event reporting.
- **External**: GNews (story source), Anthropic (LLM summarisation
  + categorisation fallback), ElevenLabs (TTS).

## Repo structure

```
backend/
  src/core/
    api/        FastAPI app — routes, middleware, schemas
    pipeline/   The processing stages — ingest, normalise, cluster,
                categorise, rank, summarise, bulletin assembly, audio
                synthesis. Each stage owns its own handlers/ and repos/.
    platform/   Cross-cutting infrastructure:
                  queue/     Postgres outbox + Redis Streams consumer
                  db/        SQLAlchemy session + models
                  storage/   audio object storage (R2 / S3)
                  providers/ external API clients (GNews)
                  config/    settings
    users/      User-account module (stub)
    workers/    Process entry points — dispatcher, consumer, scheduler
ios/
  Headlines/    Swift app — BulletinPlayer, manifest models,
                view models, views
ops/
  Start Headlines.command   Double-click to run the dev server
  Pull 50 Stories.command   Double-click to trigger ingestion
  Reset Profile.command     Double-click to reset listened-to state
                            for a profile (dev only)
```

The `platform/queue` layer is the spine of the system: pipeline stages
never call each other directly — they communicate through events
published via a Postgres outbox and consumed off Redis Streams.

## Running it locally

Requires Python 3.11, a local Postgres, and the relevant API keys
in `backend/.env` (GNews, Anthropic, ElevenLabs).

```bash
# Start the server
cd backend
./run.sh

# (or, from anywhere, after sourcing the dev aliases)
headlines

# Pull stories
./ops/"Pull 50 Stories.command"

# Reset listened-to state for a profile (dev only)
./ops/"Reset Profile.command"
```

iOS app builds from Xcode against the backend running on the same
LAN (default `192.168.1.x:8000`).

## Deployment: single-worker constraint

The Railway deployment **must run exactly one uvicorn worker**. The deploy
start command is:

```
uvicorn core.api.main:app --host 0.0.0.0 --port $PORT
```

No `--workers` flag, no `--reload` flag.

**Why this matters.** Shape B runs the event dispatcher and the Redis Streams
consumer as in-process daemon threads, started in the FastAPI lifespan handler
(`backend/src/core/api/main.py`). Launching multiple uvicorn workers would
spawn a duplicate dispatcher and consumer in each worker process, causing
events to be double-published and double-processed.

**`PYTHONUNBUFFERED=1` is required.** The in-process worker threads emit
progress and error output via `print()`. Without `PYTHONUNBUFFERED=1`,
Python buffers stdout and those log lines never reach Railway's log stream.
The environment variable must be set in the Railway service configuration.

## Status

A working, in-development personal project. The full pipeline runs
end-to-end — ingestion → clustering → categorisation → bulletin
assembly → audio generation → streaming playback on iOS with per-user
listened-to tracking.

**Deployment.** The backend is deployed on Railway, running the Shape B
single-worker process described above. The deploy pipeline is proven
end-to-end:

- Postgres is provisioned via the Railway plugin; schema migrations run
  on each deploy through an `alembic upgrade head` pre-deploy command.
- Redis is provisioned via the Railway plugin. `DATABASE_URL` and
  `REDIS_URL` are wired through Railway reference variables.
- `/health` returns 200 from the public URL
  (`https://headlines-production-5566.up.railway.app/health`).

It is deployed but not yet configured for end-to-end use — it cannot
serve a bulletin in production yet. Still to set up:

- Provider API keys (Anthropic, ElevenLabs, GNews) are not yet set in
  the Railway environment.
- Object storage for audio (`AUDIO_STORAGE_PROVIDER`, the R2/S3 vars)
  is not yet configured.
- `PUBLIC_API_BASE_URL` is not yet set, so generated manifests would
  still point at localhost.
- No ingest has been run against the production database, so it holds
  no stories yet.
- iOS still points at the dev backend (the ATS local-network exception
  is still in place); there is no TestFlight build yet.

What runs today, locally and full end-to-end:
- Segment-level audio cache (one TTS call per unique content, ever)
- Story-level deduplication and clustering
- On-demand summarisation + TTS, with full caching
- iOS streaming playback with chaptered-podcast UI
- Per-user listened-to enforcement across bulletins
- Dev ops layer (start server, pull stories, reset profile)

Next:
- Set the remaining production env vars and run a production ingest
- Point iOS at the production URL and remove the ATS exception
- TestFlight Internal distribution
- Stats / observability (cache hit rates, listening analytics)
- Hot pre-generation for popular stories (multi-user optimisation)
- Polish on the iOS player UI

## Notes

Built with AI-assisted implementation. Architectural decisions,
product principles, and design judgement are mine; significant
portions of the implementation were written with the help of LLM
coding agents under my direction.
