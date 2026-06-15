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

backend/
src/core/
api/                 FastAPI endpoints
pipeline/            Ingestion, clustering, categorisation,
summarisation, audio synthesis
models/              Domain models
workers/             Event consumers
ios/
Headlines/             Swift app — player, manifest models,
BulletinPlayer, view models, views
ops/
Start Headlines.command   Double-click to run the dev server
Pull 50 Stories.command   Double-click to trigger ingestion
Reset Profile.command     Double-click to reset listened-to state
for a profile (dev only)

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

## Status

This is a working, in-development personal project. The full pipeline
runs end-to-end: ingestion → clustering → categorisation → bulletin
assembly → audio generation → streaming playback on iOS with full
listened-to tracking.

What's working:
- Segment-level audio cache (one TTS call per unique content, ever)
- Story-level deduplication and clustering
- On-demand summarisation + TTS, with full caching
- iOS streaming playback with chaptered-podcast UI
- Per-user listened-to enforcement across bulletins
- Dev ops layer (start server, pull stories, reset profile)

What's still ahead:
- Cloud-hosted backend (currently runs on dev laptop)
- TestFlight distribution
- Stats / observability (cache hit rates, listening analytics)
- Hot pre-generation for popular stories (multi-user optimisation)
- Polish on the iOS player UI

## Notes

Built with AI-assisted implementation. Architectural decisions,
product principles, and design judgement are mine; significant
portions of the implementation were written with the help of LLM
coding agents under my direction.
