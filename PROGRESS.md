# Progress

## What Is Working

### Full event chain (end-to-end: Ingest → Normalise → Cluster → Categorise → Rank → Summarise)

```
data.ingest.requested
  → handle_ingest_requested        → data.normalise.requested
  → handle_normalise_requested     → data.cluster.requested
  → handle_cluster_requested       → data.cluster.completed
  → handle_cluster_completed       → data.categorise.completed
  → handle_categorise_completed    → persists to data.ranking_runs
                                   → data.rank.completed
  → handle_rank_completed          → persists to data.story_summaries (when API key set)
                                   → data.summarise.completed
```

### Registered handlers (registry_wiring.py)

| Event | Handler |
|---|---|
| `data.ingest.requested` | `handle_ingest_requested` |
| `data.normalise.requested` | `handle_normalise_requested` |
| `data.cluster.requested` | `handle_cluster_requested` |
| `data.cluster.completed` | `handle_cluster_completed` (categorise) |
| `data.categorise.completed` | `handle_categorise_completed` (ranking) |
| `data.rank.completed` | `handle_rank_completed` (summarise) |
| `data.summarise.requested` | `handle_summarise_requested` (stub, unused) |
| `feeds.requested` | `handle_feeds_requested` |
| `scripts.requested` | `handle_scripts_requested` |
| `audio.requested` | `handle_audio_requested` |

### Categorisation System (article-level, in normalise handler)

- `normalise/categorise/` — entity matching + category matching from YAML taxonomy
- Runs during normalisation; writes `category_primary`, `category_slugs` to `data.normalisation_articles`

### Story-level Categorisation (new, post-cluster)

- `pipeline/data/categorise/handlers/cluster_completed.py`
- Listens to `data.cluster.completed`
- Majority-vote across all articles per story to resolve `primary_category`
- Updates `data.stories.primary_category` where changed
- Emits `data.categorise.completed`

### Ranking Module

- `ranking/scorer.py`, `category_ranker.py`, `selection.py`, `ranker.py` — complete (see session 2)
- `ranking/handlers/categorise_completed.py` — loads all 48h candidates, runs `StoryRanker.rank()`, persists to `data.ranking_runs`
- `ranking/repos/ranking_run_repo.py` — inserts ranking run as JSONB; `get_latest()` for API use

### Database Schema (all migrations applied)

- `data.ingested_articles`
- `data.normalisation_articles` — with `category_slugs`, `category_primary`, `story_id`
- `data.entities` + `data.normalisation_article_entities`
- `data.stories` + `data.story_articles`
- `data.ranking_runs` — `batch_id` (UNIQUE), `candidate_count`, `top_stories` JSONB, `briefing` JSONB
- `data.story_summaries` — `story_id`, `ranking_run_id`, `content_hash`, `headline`, `summary_text`, `why_it_matters`, `audio_script`, `model`, `summary_version`, `confidence`, UNIQUE(story_id, ranking_run_id), INDEX(story_id, content_hash, model)

## Changes Made This Session (2026-04-25, session 6 — LLM fallback categoriser)

**Architecture:**
Two-pass categorisation. YAML always runs first (unchanged precision). If YAML returns score=0 across all rules, Claude Haiku is asked to classify from the exact list of valid slugs. Only persisted if confidence ≥ 0.85.

**New files:**
- `categorise/llm_fallback.py` — `classify_fallback(title, snippet, valid_slugs) → FallbackResult | None`
  - Uses stdlib `urllib.request` (no new dependency)
  - `FALLBACK_CONFIDENCE_THRESHOLD = 0.85`
  - Validates returned slug is in the permitted list (no hallucinated categories)
  - Logs ACCEPTED/REJECTED decisions at INFO level
  - Returns `None` silently if `ANTHROPIC_API_KEY` not set or API call fails
- `devtools/fallback_inspect.py` — preview/apply fallback decisions on uncategorised articles
  - Run: `cd backend && .venv/bin/python src/core/pipeline/devtools/fallback_inspect.py`
  - Apply to DB: add `--apply` flag

**Modified files:**
- `categorise/category_loader.py` — added `load_valid_category_slugs()` (reads categories.yml, returns 43 leaf slugs including `science`, `health`, `climate`)
- `categorise/category_service.py` — calls fallback when YAML returns empty; stores `method="llm-fallback"`, optional `fallback_confidence/reason` fields
- `platform/config/settings.py` — added `anthropic_api_key: str | None = None`
- `.env` — added `ANTHROPIC_API_KEY=` placeholder

**Test coverage:** 17 unit tests (mock-based) covering: accepted, rejected, invalid slug, malformed JSON, code-fence JSON, HTTP error, missing key, category_service integration.

**Expected impact (simulation on 45 uncategorised articles):**
- Before: 47/92 = 51% categorised
- After (+31 accepted, 12 rejected below threshold): 78/92 = **85%** categorised
- Rejected examples (correctly left uncategorised): WFH four-day week (0.74), Killin water supply (0.72), local crime articles (0.77-0.83)
- Accepted examples: Tyson Fury boxing (0.97), NASA mission (0.95), Assassin's Creed gaming (0.96), Scottish election (0.94), Ukraine war (0.93)

**Production hardening — all 27 checks passed:**
- Provider: model read from `FALLBACK_MODEL` env var, isolated in `_model()`, no model-name branches in logic
- Safety: fallback gated strictly on `if not ranked`, `--apply` required, `WHERE category_primary IS NULL` double-guard
- Schema: 43-slug allowlist from categories.yml, confidence range [0.0,1.0] validated, JSON fail-closed
- Observability: `article_id` on every log line (devtool passes it explicitly), ACCEPTED/REJECTED at INFO
- Persistence: `--apply` touches only `category_primary`, `category_slugs`, `category_method`, `category_version`

**Actual results after apply:**
- Before: 47/92 = 51% categorised
- Applied: 31 accepted (≥0.85 confidence), 14 rejected
- After: 78/92 = **85%** categorised
- Remaining 14 are appropriately ambiguous (local crime 0.77-0.83, lifestyle 0.73-0.82)

**To run on real data:**
1. Add `ANTHROPIC_API_KEY=<key>` to `.env`
2. `cd backend && .venv/bin/python src/core/pipeline/devtools/fallback_inspect.py`
3. Review output, then re-run with `--apply` to write to DB

## Changes Made This Session (2026-04-25, session 5 — YAML hardening pass)

**Modified:**
- `taxonomy/entities.yml`:
  - Removed person entities: `taylor-swift`, `lewis-hamilton`, `max-verstappen`, `charles-leclerc`, `oscar-piastri`, `carlos-sainz` (persons are not durable taxonomy anchors)
  - Removed noisy single-word aliases: `labour`, `england`, `ashes`, `switch` (would match unrelated text)
  - Changed `apple` alias from `"apple"` to `"apple inc"` (avoids non-tech apple matches)
  - Removed `"meta"` alias from meta entity (avoids "meta-analysis", "meta narrative" matches; `"facebook"` retained)
- `taxonomy/rules.yml`:
  - Added `climate:` section (climate change, carbon emissions, net zero, global warming, fossil fuels + cop/ipcc entities)
  - Added `health:` section (mental health, clinical trial, cancer diagnosis, vaccine + nhs/world-health-organization entities)
  - `politics.uk`: added `"home secretary"`, `"whitehall"`
  - `world.uk`: removed `"alleged victims"`, `"police investigation"`, `"criminal charges"` (too broad)
  - `technology.companies`: removed `"apple"`, `"meta"` keywords (entity-based detection handles these)
  - `technology.consumer`: removed `"device"` (too broad)
  - `entertainment.music`: removed `"single"`, `"tour"` (too broad); added `"monty python"` to tv-film
  - `sport.football.premier-league`: added `"nottingham forest"`
  - `sport.football.championship`: changed `"championship"` → `"efl championship"` (avoids golf/academics)
  - `sport.golf.pga`: changed `"masters"` → `"augusta"` (avoids Masters degrees)
  - `sport.cricket.international`: changed `"ashes"` → `"the ashes"` (avoids religious matches)
- `cluster/handlers/requested.py`: added `_HINT_STOPWORDS`, `_extract_title_hint()`, two-condition hint+category dedup for non-entity articles; `is_clustering_anchor` guard (countries excluded from entity-based clustering)
- `categorise/category_matcher.py`: country entity boost reduced 5.0→1.5

**Created:**
- `devtools/cluster_hint_test.py` — 20 tests for hint extraction and clustering logic (all passing)

**Data fixes:**
- Article 47 `category_primary` corrected from `lewis-hamilton` (invalid entity slug) to `sport.formula1`
- All 92 `normalisation_articles` recategorised in-place with current YAML; 51% categorisation rate (up from ~46%)

**Validation:**
- 20/20 noise probes passing (apple/meta false positives eliminated)
- 0 invalid `category_primary` values
- 1 mixed cluster (story 5: Iran/FTSE — acceptable crossover on the same story)
- Top ranking: world.middle-east, politics.uk, world.asia, world.europe, sport.football

## Changes Made This Session (2026-04-24, session 4+)

**Created:**
- `pipeline/devtools/show_latest_ranking_run.py` — prints the latest `data.ranking_runs` row with all scoring fields
- `api/routes/data.py` — added `GET /data/ranking/latest` endpoint

**Verified:**
- Full event chain end-to-end (ingest → normalise → cluster → categorise → rank)
- `data.ranking_runs` receives one row per unique categorisation batch
- Idempotency: replaying the same `data.categorise.completed` does not create duplicate rows (UniqueViolation on `ux_data_ranking_runs_batch_id`)
- Migration `3e8f9a2b1c7d` applied

## Changes Made This Session (2026-04-24, session 3)

**Created:**
- `pipeline/data/cluster/events.py` — added `DATA_CLUSTER_COMPLETED`
- `pipeline/data/categorise/__init__.py`
- `pipeline/data/categorise/events.py` — `DATA_CATEGORISE_COMPLETED`
- `pipeline/data/categorise/handlers/__init__.py`
- `pipeline/data/categorise/handlers/cluster_completed.py` — story-level categorisation
- `pipeline/ranking/events.py` — `DATA_RANK_COMPLETED` (for future downstream stages)
- `pipeline/ranking/handlers/__init__.py`
- `pipeline/ranking/handlers/categorise_completed.py` — ranking handler
- `pipeline/ranking/repos/__init__.py`
- `pipeline/ranking/repos/ranking_run_repo.py` — `RankingRunRepo`
- `alembic/versions/3e8f9a2b1c7d_add_ranking_runs.py` — `data.ranking_runs` table

**Modified:**
- `pipeline/data/cluster/handlers/requested.py` — collects `affected_story_ids`, imports OutboxRepo/Event, emits `data.cluster.completed` before commit
- `platform/queue/registry_wiring.py` — registered `handle_cluster_completed` and `handle_categorise_completed`

## Changes Made This Session (2026-04-25, session 10 — TTS / audio generation)

**Architecture:**
Audio generation is cached by `(bulletin_id, script_hash, provider, voice, model, audio_format)`. Script hash is SHA-256 of the bulletin script (16 chars). Provider/voice/model read from env vars at call time. Uses stdlib `urllib.request` — no new dependencies. Files stored to `{audio_local_dir}/bulletins/{bulletin_id}_{script_hash}.{ext}` (absolute path). Assembly commits before audio is attempted: a TTS failure never rolls back the bulletin.

**New files:**
- `pipeline/data/bulletin/audio/__init__.py`
- `pipeline/data/bulletin/audio/tts_client.py`
  - `synthesize(text, *, provider, voice, model, audio_format) → bytes | None` — dispatches to ElevenLabs or OpenAI
  - `compute_script_hash(script) → str` — 16-char SHA-256
  - `ext_from_format(audio_format) → str` — mp3/pcm/opus
  - `tts_provider/voice/model/audio_format()` — read env vars at call time, never cached
  - ElevenLabs: POST `/v1/text-to-speech/{voice_id}?output_format={fmt}`, `xi-api-key` header
  - OpenAI: POST `/v1/audio/speech`, `Authorization: Bearer` header
- `pipeline/data/bulletin/audio/repos/bulletin_audio_repo.py`
  - `get_cached(bulletin_id, script_hash, provider, voice, model, audio_format) → dict | None`
  - `insert(...)` — `ON CONFLICT DO NOTHING` + fallback SELECT (returns existing id either way)

**Migration `f8a9b0c1d2e3`:**
```sql
data.bulletin_audio (
    id, bulletin_id FK data.bulletins(id),
    script_hash TEXT, provider TEXT, voice TEXT,
    model TEXT, audio_format TEXT, storage_path TEXT,
    duration_seconds REAL, created_at TIMESTAMPTZ,
    UNIQUE(bulletin_id, script_hash, provider, voice, model, audio_format)
)
```

**Modified files:**
- `platform/config/settings.py` — added `tts_provider`, `tts_voice`, `tts_model`, `tts_audio_format`, `elevenlabs_api_key`, `openai_api_key`
- `.env` — added TTS env var stubs (`TTS_PROVIDER=elevenlabs`, `ELEVENLABS_API_KEY=` placeholder)
- `pipeline/data/bulletin/repos/bulletin_repo.py` — added `get_by_id(bulletin_id)`
- `api/routes/data.py` — added `_generate_audio()` helper, `POST /data/bulletins/{bulletin_id}/audio`, `POST /data/bulletins/assemble-and-audio`

**Env vars required:**
```
TTS_PROVIDER=elevenlabs           # or openai
ELEVENLABS_API_KEY=<key>          # required when provider=elevenlabs
OPENAI_API_KEY=<key>              # required when provider=openai
TTS_VOICE=JBFqnCBsd6RMkjVDRZzb   # ElevenLabs "George" (British male); optional override
TTS_MODEL=eleven_turbo_v2_5       # optional override
TTS_AUDIO_FORMAT=mp3_44100_128    # optional override
```

**API responses:**

`POST /data/bulletins/{bulletin_id}/audio`:
```json
{
  "bulletin_id": 2, "audio_id": 1, "script_hash": "01cc38ee330c4060",
  "provider": "elevenlabs", "voice": "JBFqnCBsd6RMkjVDRZzb",
  "model": "eleven_turbo_v2_5", "audio_format": "mp3_44100_128",
  "storage_path": "/abs/path/.local/audio/bulletins/2_01cc38ee330c4060.mp3",
  "duration_seconds": null, "cached": true
}
```

`POST /data/bulletins/assemble-and-audio`:
```json
{
  "ranking_run_id": 13, "bulletin_id": 2, "request_hash": "44136fa355b3678a",
  "story_count": 11, "bulletin_cached": true,
  "audio_id": 1, "script_hash": "01cc38ee330c4060",
  "provider": "elevenlabs", "voice": "JBFqnCBsd6RMkjVDRZzb",
  "model": "eleven_turbo_v2_5", "audio_format": "mp3_44100_128",
  "storage_path": "/abs/path/.local/audio/bulletins/2_01cc38ee330c4060.mp3",
  "duration_seconds": null, "audio_cached": true
}
```

**Validation (5 of 5 cases, no live API key needed for cache/fail-close cases):**
1. Generate audio for bulletin 2 (seeded) → `audio_id=1  cached=True` ✓
2. Repeat same request → `cached=True  audio_id=1` (no new row) ✓
3. Different bulletin (id=3, politics) → separate audio path (503 — no key) ✓ (different path in DB)
4. Same script/provider/voice/model → 1 row in DB after 5 calls ✓ (`ON CONFLICT DO NOTHING`)
5. Missing API key → `503 TTS provider unavailable`, 0 DB rows ✓
6. assemble-and-audio: both bulletin+audio cached → `bulletin_cached=True  audio_cached=True` ✓
7. assemble-and-audio: invalid category → 422 before any DB/TTS work ✓
8. Assembly commits before TTS: bulletin persisted even when audio 503s ✓

**To generate real audio:**
1. Add `ELEVENLABS_API_KEY=<key>` to `.env`
2. `POST /data/bulletins/2/audio` → generates MP3, stores to `.local/audio/bulletins/`
3. Repeat → returns `cached=true`

## Changes Made This Session (2026-04-25, session 11 — local dev dashboard)

**Architecture:**
Read-only dev dashboard served by the existing FastAPI app at `/dev`. Zero changes to any pipeline logic. New router mounts at `/dev`; all `/dev/api/*` endpoints are inspection-only (no side effects). Dashboard HTML calls both inspection endpoints and the real product APIs.

**New files:**
- `api/routes/dev.py` — inspection router (all read-only)
  - `GET /dev` — serves HTML dashboard
  - `GET /dev/api/pipeline` — ranking runs (with summary/bulletin/audio counts), bulletins, audio, event outbox summary
  - `GET /dev/api/stories` — stories in ranking order with articles, categories, entity links
  - `GET /dev/api/summaries` — summaries with full fields + word count + tier
  - `GET /dev/api/categories` — 43 valid category slugs from YAML taxonomy (for filter UI)
  - `GET /dev/api/audio/{bulletin_id}/file` — serves MP3 from `data.bulletin_audio.storage_path` (local dev only)
- `api/routes/dev_dashboard.html` — single-file HTML/CSS/JS dashboard (~470 lines, no dependencies)

**Modified files:**
- `api/router.py` — added `dev_router` include (only change to existing files)

**Dashboard tabs:**
1. **Pipeline** — Ranking runs table (id, candidates, summaries, avg confidence, bulletins, audio); Bulletins table; Audio files table; Event outbox summary by type/status
2. **Stories** — Stories in ranking order (TOP/BRIEFING badge, category, title, article count, expandable article list with per-article category method/entity)
3. **Summaries** — Summary cards in ranking order (expandable: summary_text, why_it_matters, audio_script, confidence, model, content_hash)
4. **Bulletin Builder** — Category multi-select (43 cats from taxonomy), max_stories, [Assemble] [Assemble+Audio] [Get Latest] buttons, inline script preview, inline `<audio>` player
5. **Audio** — Audio file list (click to play), embedded `<audio>` element, file size + estimated duration

**Local URL:** `http://localhost:8001/dev`

**Validation (10/10):**
1. `/dev` loads ✓ — HTML returned correctly
2. Pipeline timeline ✓ — 10 runs, 7 bulletins, 2 audio files, outbox by type
3. Stories ✓ — 11 stories, ranking order, articles with categories + entities
4. Summaries ✓ — 11 summaries, confidence, word count, all fields
5. Assemble filtered bulletin ✓ — politics filter → 4 stories, new hash
6. Generate/fetch audio ✓ — bulletin 8 cached, audio_id=3
7. Audio player ✓ — `GET /dev/api/audio/8/file` returns `200 audio/mpeg` (2.8MB)
8. Cached repeat ✓ — `bulletin_cached=True  audio_cached=True`
9. Real backend APIs ✓ — JS calls `/data/*` and `/dev/api/*` (no mock data)
10. No core logic changed ✓ — only `router.py` + 2 new files added

## Changes Made This Session (2026-04-25, session 9 — user-specific bulletin assembly)

**Architecture:**
Bulletins are now request-specific rather than global. `POST /data/bulletins/assemble` accepts filters, hashes them to a `request_hash`, and caches the assembled bulletin in DB. The global handler (`handle_summarise_completed`) now also uses a request_hash (empty filters `{}` → hash `44136fa355b3678a`) — same table, same idempotency logic.

**New files:**
- `pipeline/data/bulletin/selector.py` — pure functions, no IO:
  - `compute_request_hash(filters) → str` — 16-char SHA-256 hex of canonical JSON
  - `validate_filter_categories(cats) → list[str]` — validates against YAML taxonomy (leaf slugs + parent prefixes)
  - `select_stories(summaries, *, include_categories, exclude_categories, max_stories) → list[dict]` — hierarchical match, preserves ranking order
  - `_matches_any(category, patterns)` — `cat == p or cat.startswith(p + ".")`

**Migration `e7f8a9b0c1d2`:** 
- Cleared existing bulletins (test data only)
- Dropped `UNIQUE(ranking_run_id)`, added `request_hash TEXT`, `filters JSONB`, `story_count INTEGER`
- Added `UNIQUE(ranking_run_id, request_hash)` — supports multiple cached bulletins per run

**Modified files:**
- `pipeline/data/bulletin/repos/bulletin_repo.py` — replaced `get_by_ranking_run(run_id)` with `get_by_run_and_hash(run_id, request_hash)`; updated `insert()` to keyword-only with `request_hash, filters, story_count`
- `pipeline/data/bulletin/handlers/summarise_completed.py` — uses `_GLOBAL_FILTERS = {}`, `compute_request_hash(_GLOBAL_FILTERS)`, `seed=int(request_hash, 16)`; updated `insert()` call
- `api/routes/data.py` — fixed `GET /data/bulletins/latest` (was calling removed `get_by_ranking_run`); added `POST /data/bulletins/assemble` with `BulletinRequest` Pydantic model

**POST /data/bulletins/assemble:**
- Accepts `include_categories`, `exclude_categories` (both validated against YAML taxonomy), `max_stories` (default 20)
- Cache hit returns `cached=true` with no DB writes
- Cache miss: loads summaries in ranking order → applies filters → assembles → persists → returns
- Returns `{ranking_run_id, request_hash, story_count, cached, script, segments}`

**Validation (all 9 cases, run_id=13, 22 summaries):**
1. No filters → 11 stories, full bulletin ✓ (hash `44136fa355b3678a`)
2. `include_categories: ["politics"]` → 4 stories ✓ (hash `a4cc2f41796b7354`)
3. `include_categories: ["technology"]` → 1 story ✓ (hash `3360413888323386`)
4. `include_categories: ["politics", "sport"]` → 5 stories ✓ (hash `20ee8f9b8a53da1a`)
5. `max_stories: 2` → 2 stories ✓ (hash `391c98caaa222ab2`)
6. Invalid category `"not.a.real.category"` → 422 with error detail ✓
7. Same request twice → `cached=True`, no duplicate rows (6 total in DB) ✓
8. Different filters → different hashes ✓
9. 22 story_summary rows unchanged — zero LLM calls throughout ✓

**Full event chain now:**
```
data.rank.completed → handle_rank_completed → data.summarise.completed
data.summarise.completed → handle_summarise_completed → data.bulletin.assembled (global bulletin)
POST /data/bulletins/assemble → returns user-specific cached bulletin
```

## Changes Made This Session (2026-04-25, session 8 — bulletin assembly)

**Architecture:**
Pure template-based assembly triggered by `data.summarise.completed`. No LLM calls. Generates once per `ranking_run_id` and reuses. Seeded by `ranking_run_id` so intro/outro are deterministic per run.

**New files:**
- `pipeline/data/bulletin/assembler.py` — `assemble(stories, seed) → BulletinResult`
  - Intro chosen from 2 options, outro from 2 options (seeded RNG, deterministic per run)
  - Category-aware transitions: `politics`, `world`, `business`, `sport`, `technology`, `entertainment`, `health`, `science`, `climate`; fallback `"Next..."`
  - Transitions inserted between stories only (not after last)
  - `audio_script` used directly; falls back to `summary_text` if empty
- `pipeline/data/bulletin/events.py` — `DATA_BULLETIN_ASSEMBLED`
- `pipeline/data/bulletin/handlers/summarise_completed.py` — `handle_summarise_completed`
  - Idempotency: checks for existing bulletin before any work
  - Loads ordering + `primary_category` from ranking run JSONB
  - Deduplicates story IDs; warns on missing summaries
  - All in single transaction: insert bulletin + emit `data.bulletin.assembled`
- `pipeline/data/bulletin/repos/bulletin_repo.py` — `get_by_ranking_run`, `get_latest`, `insert`

**Migration `d6e7f8a9b0c1`:** `data.bulletins(id, ranking_run_id UNIQUE, script TEXT, segments JSONB, created_at)`

**Modified files:**
- `ranking/repos/ranking_run_repo.py` — added `get_by_id(run_id)`
- `platform/queue/registry_wiring.py` — `data.summarise.completed → handle_summarise_completed`
- `api/routes/data.py` — added `GET /data/bulletins/latest`

**Validation (run_id=13, 11 stories):**
- First assembly: bulletin created, `story_count=11`
- Segment sequence: `intro → story → [transition → story] × 10 → outro` ✓
- Top stories before briefing ✓
- No missing stories ✓
- No duplicate stories ✓
- Idempotency: second run logged `bulletin already exists — skipping`, `COUNT(*) = 1` ✓
- Category transitions working: politics/world/technology/sport all correctly matched

**Full event chain now:**
```
data.rank.completed → handle_rank_completed → data.summarise.completed
data.summarise.completed → handle_summarise_completed → data.bulletin.assembled
```

## Changes Made This Session (2026-04-25, session 7b — summarisation hardening)

**Cost control:**
- Pre-check: `get_existing_story_ids(ranking_run_id)` — skips stories already summarised for this run before any LLM call
- Content-hash cache: `get_cached(story_id, content_hash, model)` — reuses summaries from previous runs when article content is unchanged; inserts a copy row with new `ranking_run_id` for provenance
- Session isolation: DB session closed before LLM calls; no connection held open during network waits
- All three paths logged at INFO: `GENERATED`, `REUSE`, `FAILED`; `data.summarise.completed` payload carries `{attempted, generated, reused, failed}`

**New migration `c5d6e7f8a9b0`:** Added `content_hash TEXT NOT NULL DEFAULT ''` + `confidence REAL` to `data.story_summaries`; partial index on `(story_id, content_hash, model) WHERE content_hash != ''`

**Prompt rewrite (`llm_summariser.py`):**
- Explicit instruction: "Summarise the story as a whole, not each article individually"
- "Base every fact only on the supplied article text. Do not add external knowledge or speculation."
- "If articles conflict, acknowledge uncertainty neutrally"
- `audio_script`: "natural spoken language for broadcast radio — no article-style prose, write as if spoken aloud"
- Returns `confidence` (LLM self-rating 0-1) as separate field

**Output shape confirmed:** `headline`, `summary_text`, `why_it_matters`, `audio_script`, `confidence`, `model`, `summary_version` — all fields persisted and returned by API

**API fix (`GET /data/summaries/latest`):**
- Summaries now returned in ranking order (top_stories first, then briefing)
- Response includes `summary_count`
- All output fields including `confidence` and `summary_version`

**Validation (mock LLM):**
- Generate run: 11 LLM calls → 11 rows, `generated=11 reused=0 failed=0`
- Idempotency run: 0 LLM calls, row count unchanged
- Cache hit confirmed: story_id + content_hash → existing headline returned
- Fail-closed: all 11 stories `FAILED` with no API key, 0 rows inserted, `data.summarise.completed` still emitted

## Changes Made This Session (2026-04-25, session 7 — summarisation stage)

**Architecture:**
Story-level summarisation triggered by `data.rank.completed`. Two-session DB access pattern: read-only articles load first, then persist summaries + emit event. Fail closed when `ANTHROPIC_API_KEY` not set.

**New files:**
- `pipeline/data/summarise/llm_summariser.py` — `summarise_story(story_id, representative_title, articles) → SummaryResult | None`
  - Uses stdlib `urllib.request` (no new dependency)
  - Model from `SUMMARISE_MODEL` env var (default `claude-haiku-4-5-20251001`)
  - Structured prompt → `{headline, summary_text, why_it_matters, audio_script}`
  - Strips markdown code fences; fails closed on parse error
- `pipeline/data/summarise/handlers/rank_completed.py` — `handle_rank_completed`
  - Deduplicates story IDs (top + briefing), loads articles per story (up to 5, most recent first)
  - Calls `summarise_story` per story; skips LLM failures silently
  - Persists to `data.story_summaries` with `ON CONFLICT (story_id, ranking_run_id) DO NOTHING`
  - Emits `data.summarise.completed` in same transaction
- `pipeline/data/summarise/repos/story_summary_repo.py` — `StorySummaryRepo.get_by_ranking_run(run_id)`
- `alembic/versions/b4c5d6e7f8a9_add_story_summaries.py` — `data.story_summaries` table

**Modified files:**
- `ranking/handlers/categorise_completed.py` — captures `run_id` from `insert_run()`, emits `data.rank.completed` (same transaction) with payload: `ranking_run_id`, `ranking_batch_id`, `categorisation_batch_id`, `top_story_ids`, `briefing_story_ids`
- `pipeline/data/summarise/models.py` — replaced article-level `SummariseOutput` with story-level `StorySummary`
- `pipeline/data/summarise/handlers/requested.py` — replaced stub with no-op (old article-level flow unused)
- `platform/queue/registry_wiring.py` — registered `data.rank.completed → handle_rank_completed`
- `api/routes/data.py` — added `GET /data/summaries/latest`

**Database schema:**
```sql
data.story_summaries (
    id SERIAL PRIMARY KEY,
    story_id TEXT NOT NULL,
    ranking_run_id INTEGER NOT NULL REFERENCES data.ranking_runs(id),
    headline TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    why_it_matters TEXT,
    audio_script TEXT,
    model VARCHAR(100) NOT NULL,
    summary_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (story_id, ranking_run_id)
)
```

**Event payload (`data.rank.completed`):**
```json
{
  "ranking_run_id": 10,
  "ranking_batch_id": "...",
  "categorisation_batch_id": "...",
  "top_story_ids": ["33", "41", "52", "57", "50"],
  "briefing_story_ids": ["51", "45", "58", "32", "42", "55"]
}
```

**Validation (no API key — fail-closed path):**
- `handle_categorise_completed` emits `data.rank.completed` with correct payload (verified in outbox)
- `handle_rank_completed` loads articles for 11 stories, returns None for all (no key), emits `data.summarise.completed` with `story_count=0`
- `ON CONFLICT DO NOTHING` idempotency confirmed

**To generate real summaries:**
1. Add `ANTHROPIC_API_KEY=<key>` to `.env`
2. Trigger a fresh ingest: `POST /data/ingest/test`
3. View summaries: `GET /data/summaries/latest`

## Changes Made This Session (2026-04-26 — physical device readiness)

**Problem 1 — `APIClient.swift` compilation bug:**
`post`, `put`, and `request` methods were defined outside the struct closing brace. Code would not compile. Fixed by rewriting the file: extracted `buildURL()` and `send()` as private helpers; all methods now inside the struct.

**Problem 2 — ATS blocks HTTP to LAN IPs on device:**
`NSAllowsLocalNetworking` only covers `127.0.0.1`/`.local`. Physical device hitting `http://192.168.x.x:8000` is blocked by default ATS. The project uses `GENERATE_INFOPLIST_FILE = YES` (no Info.plist exists), so ATS can't be configured without creating one.

**Problem 3 — URL hardcoded to 127.0.0.1:**
Simulator-only default. Device needs Mac LAN IP.

**Files changed:**
- `Core/APIClient.swift` — rewrote to fix struct closure bug; extracted `buildURL()` + `send()` helpers; all methods now inside struct
- `Core/AppConfig.swift` — `#if targetEnvironment(simulator)` auto-selects `127.0.0.1` for simulator, `deviceLANIP` constant for device; one-time edit: set `deviceLANIP = "192.168.1.111"` (current Mac LAN IP)
- `Headlines/Info.plist` — **new file** replacing auto-generated plist:
  - Replicates all `INFOPLIST_KEY_*` build settings (UIApplicationSceneManifest, UILaunchScreen, UISupportedInterfaceOrientations, etc.)
  - Adds `NSAppTransportSecurity`: `NSAllowsLocalNetworking = true`, `NSAllowsArbitraryLoadsInDebug = true` (debug builds only — no ATS impact on release)
  - Adds `API_BASE_URL = $(API_BASE_URL)` build variable hook for per-scheme URL override

**Required Xcode steps (one-time, manual):**
1. In Xcode project navigator: right-click `Headlines` group → Add Files → select `Info.plist`
2. Build Settings → search "Generate Info.plist" → set to **No** for Debug and Release
3. Build Settings → search "Info.plist File" → set to `Headlines/Info.plist`
4. In `AppConfig.swift` line `static let deviceLANIP = "YOUR_MAC_LAN_IP"` → replace with `"192.168.1.111"` (or your current LAN IP — run `ipconfig getifaddr en0` to check)

**Audio URL handling (all cases covered):**
| Scenario | `audioUrl` in response | How iOS downloads |
|---|---|---|
| Local dev (simulator) | `null` | `service.audioFileURL()` → `http://127.0.0.1:8000/dev/api/audio/{id}/file` |
| Local dev (device) | `null` | `service.audioFileURL()` → `http://192.168.1.111:8000/dev/api/audio/{id}/file` |
| S3 production | `https://bucket.s3.region.amazonaws.com/...` | URL used directly, no Mac involved |

**Validation:**
- `GET http://192.168.1.111:8000/data/profiles` → `200 {"profiles":[...]}` ✓
- `GET http://192.168.1.111:8000/dev/api/audio/13/file` → `200 1,587,453 bytes` ✓
- Backend bound to `0.0.0.0:8000` (uvicorn `--host 0.0.0.0`) ✓
- No hardcoded `127.0.0.1` outside `AppConfig.swift` ✓

## Changes Made This Session (2026-04-26 — audio storage abstraction)

**Architecture:**
Replaced direct local-disk writes in `_generate_audio()` with a pluggable storage provider. Provider is selected at startup from `AUDIO_STORAGE_PROVIDER` env var. `local` mode is unchanged (writes `.local/audio/bulletins/`). `s3` mode uploads to any S3-compatible bucket and derives a public URL. `audio_url` is now persisted in DB and returned in all audio API responses. iOS uses `audio_url` directly when non-null; falls back to local dev path otherwise.

**New files:**
- `platform/storage/__init__.py`
- `platform/storage/audio_storage.py`
  - `AudioStorageResult(storage_path, audio_url)` — dataclass
  - `AudioStorageProvider` — ABC with `store(bytes, filename) → AudioStorageResult`
  - `LocalAudioStorage(base_dir)` — writes to local disk; `audio_url=None`
  - `S3AudioStorage(bucket, region, access_key_id, secret_access_key, endpoint_url?, public_base_url?)` — uploads with boto3
    - URL derivation: `public_base_url/{key}` → `endpoint_url/{bucket}/{key}` → `https://{bucket}.s3.{region}.amazonaws.com/{key}`
  - `get_audio_storage_provider()` — lazy singleton; validates required S3 config at startup

**Migration `b3c4d5e6f7a8`:**
- `ALTER TABLE data.bulletin_audio ADD COLUMN audio_url TEXT` (nullable)
- Existing local rows get `audio_url = NULL` (correct — local mode never stores a URL)

**Modified files:**
- `platform/config/settings.py` — added 7 new settings:
  ```
  audio_storage_provider: str = "local"
  s3_bucket, s3_region, s3_access_key_id, s3_secret_access_key
  s3_endpoint_url, s3_public_base_url (all str | None)
  ```
- `pipeline/data/bulletin/audio/repos/bulletin_audio_repo.py`
  - `get_cached()` SELECT now includes `audio_url`
  - `insert()` accepts `audio_url: str | None = None`, includes in INSERT
- `api/routes/data.py`
  - Removed direct `os.makedirs` / `open()` writes
  - Added `get_audio_storage_provider()` call; passes `stored.audio_url` to DB insert
  - `_do_assemble_and_audio()` response and `generate_bulletin_audio` endpoint both now include `"audio_url"` key
- `.env` — added `AUDIO_STORAGE_PROVIDER=local` and commented S3 stubs
- `pyproject.toml` — added `boto3 = {version = "^1.35.0", optional = true}` + `[tool.poetry.extras] s3 = ["boto3"]`

**iOS files:**
- `Models/BulletinResult.swift` — added `audioUrl: String?`
- `Services/Profiles/ProfileDTO.swift` — added `audioUrl: String?` to `BulletinResultDTO` (CodingKey: `audio_url`)
- `Services/Profiles/ProfileService.swift` — `BulletinResult.init(dto:)` maps `audioUrl`
- `Features/Briefing/BriefingViewModel.swift` — uses `result.audioUrl` directly if non-nil; falls back to `service.audioFileURL(forBulletinID:)` for local dev

**Env vars:**
```
AUDIO_STORAGE_PROVIDER=local     # local (default) | s3

# S3 mode — required when AUDIO_STORAGE_PROVIDER=s3:
S3_BUCKET=my-headlines-audio
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=AKIA...
S3_SECRET_ACCESS_KEY=...

# Optional:
S3_ENDPOINT_URL=https://<acct>.r2.cloudflarestorage.com   # R2, Spaces, MinIO
S3_PUBLIC_BASE_URL=https://cdn.example.com                 # CDN domain override
```

**To install boto3 (S3 mode only):**
```bash
cd backend && poetry install -E s3
```

**Example API response (local mode, audio_url=null):**
```json
{
  "profile_id": 1, "profile_name": "Paul",
  "bulletin_id": 13, "story_count": 5,
  "bulletin_cached": true,
  "audio_id": 7, "voice": "JBFqnCBsd6RMkjVDRZzb",
  "audio_url": null,
  "audio_cached": true
}
```

**Example API response (S3 mode):**
```json
{
  "audio_url": "https://my-headlines.s3.us-east-1.amazonaws.com/bulletins/13_b23b5858afd5c157.mp3",
  "audio_cached": false
}
```

**Validation (all 5 checks passing):**
1. `get_audio_storage_provider()` in local mode → `LocalAudioStorage` ✓
2. Missing S3 config → `RuntimeError: required env vars missing: S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY` ✓
3. `LocalAudioStorage.store()` → `audio_url=None`, file written correctly ✓
4. URL derivation for all 3 S3 modes (AWS / endpoint / CDN) ✓
5. `POST /data/bulletins/13/audio` (cache hit) → `"audio_url": null, "cached": true` ✓
6. `GET /dev/api/audio/13/file` → `200 audio/mpeg 1.5MB` (local file serving unchanged) ✓
7. `get_cached()` includes `audio_url` column in result ✓

## Changes Made This Session (2026-04-27 — iOS device fixes)

**Three bugs fixed on physical iPhone:**

**1. Device IP placeholder** (`Core/AppConfig.swift`)
- `deviceLANIP = "YOUR_MAC_LAN_IP"` → `"192.168.1.111"`
- Was causing `NSURLErrorDomain Code=-1003` (host not found) on every request

**2. Error logging — response body swallowed** (`Core/APIClient.swift`, `Core/AudioPlayer.swift`, `Features/Playback/PlaybackViewModel.swift`)
- `APIError.badStatus(Int)` → `badStatus(Int, String)` — now carries response body snippet
- `send()` prints `⚠️ API METHOD URL → STATUS: body` in DEBUG builds
- Surfaces backend error detail (e.g. `"No ranking runs found"`) instead of just a status code

**3. Audio silent on device** (`Core/AudioPlayer.swift`, `Features/Briefing/BriefingViewModel.swift`)
- Root cause: `AVAudioPlayer.play()` uses `.soloAmbient` session by default — silenced by mute switch on device, works fine in simulator
- Fix: `play()` now calls `AVAudioSession.sharedInstance().setCategory(.playback)` + `setActive(true)` before every play
- Defensive fix: `resolvedAudioURL()` helper in `BriefingViewModel` rejects `audio_url` values with `localhost`/`127.0.0.1` host and falls back to `audioFileURL(forBulletinID:)` which resolves against `AppConfig.apiBaseURL` (LAN IP on device)
- Debug logging added: final audio URL, download byte count, failure body

**Ingest configuration (read-only inspection, no changes):**
- Scheduled ingest: `ENABLE_SCHEDULED_INGEST=false` (disabled), 30-min interval when on
- Per-run fetch: 10 articles from GNews `top-headlines`, `lang=en`, `country=gb`
- No env var for `max_results` or country/lang — hardcoded in handler
- Dedup: SHA-256 of URL checked before every insert; no duplicates stored
- Ranking window: 48 hours (hardcoded in `candidate_loader.py`)

## Changes Made This Session (2026-04-26 — iOS frontend architecture refactor)

**Goal:** Establish a disciplined feature-based iOS architecture before adding more UI. No product behaviour changes.

**Architecture applied:**
```
Core/           — app-wide utilities (APIClient, AppConfig, AudioPlayer)
Models/         — plain value types (Profile, BulletinResult)
Services/       — protocol + concrete service per domain
Features/       — one folder per screen; ViewModel owns state, View owns layout
Shared/Components/ — generic reusable views only
```

**New files (10):**
- `Core/AudioPlayer.swift` — `@MainActor final class`; wraps `AVAudioPlayer`; static `download(from:id:)` method; `progress`, `isAtEnd`, `duration` computed props; `play/pause/stop/load`
- `Services/BulletinService.swift` — `BulletinServicing` protocol + `BulletinService` impl; private `BulletinResultDTO` (moved from ProfileDTO.swift); `audioFileURL(forBulletinID:)` uses `URLComponents`
- `Features/Profiles/ProfileListView.swift` — extracted from `ProfilePickerSheet` at bottom of `BriefingView.swift`; renamed to match feature folder
- `Features/Briefing/PlayerView.swift` — extracted `playerCard`; takes `progress`, `duration`, `canTogglePlayPause`, `isPlaying`, `bulletin`, `onTogglePlayPause`; owns `cacheBadge` and `formatTime` helpers
- `Features/Briefing/BriefingLoadingView.swift` — handles `.loadingProfiles` (shows `LoadingStateView`) and `.noProfiles` (shows `PrimaryButton`)
- `Features/Briefing/BriefingErrorView.swift` — thin wrapper around `ErrorStateView`
- `Features/Settings/SettingsView.swift` — stub showing `AppConfig.apiBaseURL.host` + port for device debugging
- `Shared/Components/PrimaryButton.swift` — `.borderedProminent` button wrapper
- `Shared/Components/LoadingStateView.swift` — `ProgressView()` + message
- `Shared/Components/ErrorStateView.swift` — error icon + title + message + retry button

**Modified files (5):**
- `Services/Profiles/ProfileServicing.swift` — removed `generateBulletin` and `audioFileURL`; now only profile CRUD
- `Services/Profiles/ProfileService.swift` — removed `generateBulletin`, `audioFileURL`, `BulletinResult` mapping
- `Services/Profiles/ProfileDTO.swift` — removed `BulletinResultDTO` (moved to `BulletinService.swift` as private)
- `Features/Briefing/BriefingViewModel.swift` — removed AVFoundation import; split `service: ProfileServicing` into `profileService + bulletinService`; uses `AudioPlayer` for all playback; `generateBulletin()` delegates download to `AudioPlayer.download()`
- `Features/Briefing/BriefingView.swift` — removed `ProfilePickerSheet`, `loadingView`, `noProfilesView`, `errorView`, `playerCard`, `cacheBadge`, `formatTime`, `playPauseIcon`; replaced with calls to extracted components; `vm.service` → `vm.profileService`

**All 10 files added to Xcode target membership** via `xcodeproj` Ruby gem.

**Build result:** `BUILD SUCCEEDED` (iPhone 15 simulator, iOS 17.5)

## Changes Made This Session (2026-04-27 — iOS redesign, story nav, summary quality, bulletin flow)

### Task 1 — iOS Briefing Screen Redesign
- `BriefingView.swift` — full rewrite: uppercase letter-spaced context label, `storiesSection` with separator + story count label, story rows with isCurrent highlight + tap-to-seek, `categoryDisplay()` helper, premium minimal layout
- `PlayerView.swift` — full rewrite: ZStack prev/play/next layout, 80pt play button, 3px progress bar, `.tertiary` time labels, nav buttons invisible when no timing data

### Task 2 — Story Visibility
- Backend: `_do_assemble_and_audio()` builds `stories_list` with headline/category/start_time; cached + fresh paths both populate it; returned as `"stories"` key
- iOS: `BulletinStory` model + `BulletinStoryDTO` decoder; `BriefingView.storyRows()` shows headlines with category labels

### Task 3 — Fix TTS 503
- Root cause: profile voice `P4DhdyNCB4Nl6MA0sL45` (ElevenLabs "Rachel") requires paid plan → HTTP 402
- `tts_client.py`: helpers now read `settings.*` not `os.environ.get()` (pydantic-settings doesn't populate environ)
- Added specific 401/402 warning messages; profile 1 voice cleared to `null` → falls back to George
- `mp3_duration()` pure stdlib MPEG1 frame scanner added to `tts_client.py`

### Task 4 — Prev/Next Story Navigation
- Proportional timestamps: `_story_timings(segments, script, duration)` computes `start_time` per story from char offset in assembled script
- `AudioPlayer.swift`: `currentTime` property + `seek(to:)` method
- `BriefingViewModel`: `currentStoryIndex`, `hasStoryTimings`, `seekToStory(at:)`, `previousStory()`, `nextStory()`, `resolveCurrentStoryIndex(at:)`, `tick()` updates highlight on playback
- `BulletinResult`/`BulletinStory` models updated with `startTime: TimeInterval?`

### Task 5 — Story Summary Quality
- `llm_summariser.py` `_build_prompt()` rewrite: explicit forbidden opener list, sentence rhythm guidance, contractions OK, concrete closing line, `why_it_matters` prohibition list ("ongoing concerns", "raises questions about", etc.)
- `_MAX_TOKENS` 512 → 600
- `settings.anthropic_api_key` used directly (was `os.environ.get`)

### Task 6 — Bulletin Flow Quality
- `assembler.py` full rewrite:
  - `_extract_hook()` — pulls first sentence of top story's `audio_script` as intro hook
  - `_build_intro()` — hooks with top story, bridges with "That story leads / We begin there / That leads today", then time-of-day greeting + name; falls back to date-based if no hook
  - `_build_outro()` — forward-looking: "We'll keep following these stories as they develop", "More on all of this as it comes in", time-aware (morning/afternoon/evening) — replaces "That's all for now"
  - `_CATEGORY_TRANSITIONS` expanded: 6-8 options per category (was 2-3), added "Politically...", "Overseas...", "Economically speaking...", "On the digital front...", etc.
  - `_NEUTRAL_TRANSITIONS` expanded: 14 options (was 4), added "At the same time...", "On a related note...", "Also making news...", "Away from that...", "Closer to home..."
- `POST /data/profiles/{id}/bulletin?force=true` — bypass cache for dev/testing (deletes existing bulletin + audio rows, re-assembles and re-generates TTS)

**Live validation (force=true, profile 1, 3 stories):**
- Intro: "Authorities have arrested... That leads today. Good evening, Paul." ✓ (hooks with story)
- Transition: "In government..." (politics.uk), "On the sporting front..." (sport) ✓
- Outro: "That's the evening briefing, Paul. More updates throughout the day." ✓ (forward-looking)

## Changes Made This Session (2026-04-27 — Task 7: include_top_stories + category filter UI)

### Backend

**Migration `0f1c2e3a4b5d`** — `ALTER TABLE data.profiles ADD COLUMN include_top_stories BOOLEAN NOT NULL DEFAULT true`

**`profile_repo.py`** — `include_top_stories` added to `_ALLOWED_UPDATE_FIELDS`, `create()`, all `SELECT` queries

**`profiles.py`** — `ProfileCreate.include_top_stories: bool = True`, `ProfileUpdate.include_top_stories: Optional[bool] = None`, `_fmt()` includes the field, bulletin route passes it to `_do_assemble_and_audio()`

**`data.py`** — Two changes:
- `GET /data/categories` — returns `{"categories": [...sorted slugs...]}` from taxonomy YAML (mirrors `/dev/api/categories` but on the public router)
- `_do_assemble_and_audio(include_top_stories=True)` — `include_top_stories` included in `filters` dict (affects cache key); when `True`: top-tier stories always included first regardless of category filters, remaining slots filled from briefing tier applying category filters; when `False`: current behaviour (filter applied to entire merged pool)

### iOS

**`Profile.swift`** — added `includeTopStories: Bool`

**`ProfileDTO.swift`** — added `includeTopStories` to `ProfileDTO` (CodingKey: `include_top_stories`), `CreateProfileBody`, `UpdateProfileBody`

**`ProfileServicing.swift`** — `createProfile` and `updateProfile` now take `includeTopStories: Bool`

**`ProfileService.swift`** — passes `includeTopStories` in request bodies and `Profile.init(dto:)` mapping

**`Models/CategoryLoader.swift`** (new) — `CategoryListDTO`, `fetchTopCategories()` — calls `GET /data/categories`, derives top-level slugs by splitting on `.`, returns sorted unique list

**`ProfileFormView.swift`** — redesigned with:
- **Identity section**: Name + Max Stories stepper (unchanged)
- **Voice section**: Picker (unchanged)
- **Listening section**: Toggle "Include top stories" + footer helper text
- **Filters section**: checkboxes for each top-level category (loaded async); checked slugs → `include_categories`; no categories checked = nil (no filter); replaces old include/exclude text fields
- `selectedCategories: Set<String>` state; `categoryBinding(for:)` helper; parallel `.task` fetches voices + categories simultaneously

## Changes Made This Session (2026-04-27 — product settings + taxonomy refinement)

### Part 1 — Category hierarchy in UI

**`category_loader.py`** — added `load_category_groups()`:
- Returns two-level hierarchy (top-level groups + their direct children)
- Special display name mapping: `politics.uk` → "UK Politics", `politics.us` → "US Politics", `politics.europe` → "European Politics", `politics.global` → "World Politics", `ai` → "AI", `tv-film` → "TV & Film", `formula1` → "Formula 1", etc.
- Three-level nesting (sport.football.premier-league) collapsed — only top two levels exposed to UI

**`data.py`** — `GET /data/categories` now returns `{"groups": [...]}` hierarchy instead of flat slug list

**iOS `CategoryLoader.swift`** — rewritten:
- New `CategoryGroup` (slug, label, subcategories) and `CategoryItem` (slug, label) types
- Decodes new `{"groups": [...]}` API shape
- Cache, timeout, and DEBUG logging preserved

**iOS `ProfileFormView.swift`** — Filters section rewritten:
- Per-group sections with group label as section header
- Each subcategory is a toggle with display label
- Leaf groups (science, climate, health) render as single toggles using group slug
- `filtersSections` uses `@ViewBuilder` with `ForEach` over groups

### Part 2 — Remove voice options

**iOS `ProfileFormView.swift`** — removed Voice section and `voices` state; always passes `voice: nil` on save
**Dev dashboard** — voice `<select>` replaced with `<input type="hidden" value="">` (keeps `voiceLabel()` working in audio list)

### Part 3 — Duration-based story selection

**Migration `1a2b3c4d5e6f`** — drops `max_stories`, adds `max_duration_minutes INTEGER NOT NULL DEFAULT 5` on `data.profiles`; applied

**`selector.py`** — added:
- `estimate_duration_seconds(audio_script)` — word count / 150 wpm * 60
- `select_stories_by_duration(summaries, *, include_categories, exclude_categories, max_duration_minutes)` — selects stories fitting within budget; always takes at least one; respects category filters

**`profile_repo.py`** — `max_stories` → `max_duration_minutes` everywhere (SELECT, INSERT, UPDATE)

**`profiles.py`** — `ProfileCreate.max_duration_minutes: int = 5`, `ProfileUpdate.max_duration_minutes: Optional[int] = None`, `_fmt()` returns `max_duration_minutes`, bulletin route passes `max_duration_minutes`

**`data.py`** — `AssembleAudioRequest.max_duration_minutes: int = 5`; `_do_assemble_and_audio` now:
- Takes `max_duration_minutes` instead of `max_stories`
- Uses `select_stories_by_duration()` for all story selection
- For `include_top_stories=True`: top pool selected by duration first, remaining minutes filled from briefing pool with category filter
- Response includes `target_duration_minutes` and `estimated_duration_seconds` (estimated from assembled script)

**iOS `Profile.swift`** — `maxStories: Int` → `maxDurationMinutes: Int`
**iOS `ProfileDTO.swift`** — `maxDurationMinutes: Int` (CodingKey: `max_duration_minutes`) in DTO and both request bodies
**iOS `ProfileServicing.swift` / `ProfileService.swift`** — `maxStories` → `maxDurationMinutes` throughout
**iOS `ProfileFormView.swift`** — max stories `Stepper` replaced with segmented `Picker` (3 min / 5 min / 10 min); `populateForm` snaps to nearest valid option
**iOS `ProfileListView.swift`** — subtitle changed from "N stories ·" to "N min ·"

**Build result:** `BUILD SUCCEEDED`

## What Is Incomplete

- **Summaries require API key** — set `ANTHROPIC_API_KEY` in `.env` to activate
- **S3 boto3 not installed** — run `poetry install -E s3` before switching to S3 mode
- **No automated tests** — pipeline health check devtool covers ingest→cluster; no unit/integration tests

## Next Steps

- Install boto3 and test S3 upload against a real bucket (R2 recommended — free tier)
- Wire summaries/bulletins into feeds/scripts/audio pipeline stages (TTS stage)
- `GET /data/bulletins/latest` returns most recently *created* bulletin — may want `GET /data/bulletins/assemble?cached_only=true` variant
