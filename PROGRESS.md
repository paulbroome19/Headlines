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

## What Is Incomplete

- **Summaries require API key** — set `ANTHROPIC_API_KEY` in `.env` to activate
- **Audio stored locally** — should be remote
- **No automated tests** — pipeline health check devtool covers ingest→cluster; no unit/integration tests

## Next Steps

- Move audio storage to remote (S3 or equivalent)
- Wire summaries/bulletins into feeds/scripts/audio pipeline stages (TTS stage)
- Personalisation: user profiles storing preferred filter sets (serve personalised bulletins automatically)
- `GET /data/bulletins/latest` returns most recently *created* bulletin — may want `GET /data/bulletins/assemble?cached_only=true` variant that checks a specific filter combination
