# Post-demo notes — deferred TTS/voice decisions

Parked during the demo voice work (PR #160). The demo voice is FROZEN after merge;
revisit these only with explicit instruction.

## Candidate TTS models (not adopted)

- **`eleven_v3`** — prosody upgrade (more expressive English). Costs pacing (~+17%
  duration: the sample ran 6.8 min vs 5.8) and gate latency, and switching model
  triggers a full `segment_audio` cache re-render (model is in the cache key). Revisit
  if flat delivery is the top complaint post-demo.
- **`eleven_flash_v2_5`** — latency play (~75 ms first-byte class), for the first-play
  gate. Fidelity trade vs `multilingual_v2`. Not a prosody upgrade.

## Cache key vs voice settings

`data.segment_audio` is uniquely keyed on `(script_hash, voice, model, audio_format)` —
**stability/style are NOT in the key**. Changing settings in prod would mix old-settings
cached segments with new-settings fresh ones in one bulletin (audibly inconsistent).
`script_hash = sha256(segment_text)` is also the iOS `/readiness` contract, so settings
must NOT be folded into it. If prod TTS settings ever start to change (per-env or repeated
tuning), add a **`voice_settings_hash` column to the unique key** (thread `voice_settings`
through `synthesize_segments` → repo) so old/new settings coexist. Not needed while
settings are fixed — a one-time cache flush covers a single change.

## Findings worth keeping (measured during PR #160)

- **`style` is a no-op** on `eleven_multilingual_v2` AND `eleven_turbo_v2_5` for our voice:
  style 0.15 vs 0.70 renders byte-identical at any fixed seed (verified, not a seed
  artifact). The only working `voice_settings` lever is **stability**. To get expressiveness
  beyond stability you need a different model (`eleven_v3`) or voice, not `style`.
- **Prosody stitching costs ~1.4s/segment** on `multilingual_v2` (measured, interleaved):
  greeting +1410 ms (+65%), opener +1393 ms (+19%). It is text-context, so segments stay
  parallel (no serialisation) — but each is +~1.4s. Hence stitching is applied to the
  background tail only; the first-play start-pack is unstitched by default
  (`segment_synth._STITCH_START_PACK = False`). Flip to stitch the open, accepting the cost.

---

# Geographic top-stories model (deferred design — DO NOT BUILD without sign-off)

Full design for the region-aware top-stories model. **Deferred post-demo** in favour of the
rubric/window stopgap (PR: region-relative UK bar + `FRONT_PAGE_N` 70→100 + `RUBRIC_VERSION`
fingerprint invalidation), which the audit showed captures ~80% of the benefit at a fraction of
the risk. This section is the record so the model can be picked up cleanly later.

## Why deferred (audit findings, prod, 2026-07-08)
- Supply is healthy: **median 3 top stories/run** (min 0, max 8) — on the ~3-6 target.
- The "UK loses to US volume" hypothesis was **not supported**: the editor already demotes
  trivial US stories. The real gaps were (a) two over-strict UK misses (Prince Harry court
  ruling, Farage resignation — both given +1 significance yet unflagged) and (b) a
  **merit-vs-normalized-score window leak** (high-merit stories fell outside the top-70 the
  editor reviews). Both are fixed by the stopgap, not the geographic model.
- `geo_region` is **source/edition region, not subject region** (pool-derived, UK-saturated:
  all currently-flagged stories read `uk`). Unusable as a "what is this about" signal.

## Target model
The editorial pass judges significance **within a geography** — a story is a UK / US /
world(global) top story on a region-relative bar ("leads the UK news today"), not one global
competition. Aim ~3-8/day, UK never starved. Selection builds the top block as **all UK top
stories, then US, then global** (config-ordered, so it can become a per-user preference later).

## Decisions
- **(a) Region source — the editorial LLM, not `geo_region`.** The pass emits `region ∈
  {uk,us,world}` per flagged top story in the SAME call (it already reads headline+snippet — the
  best subject-region judge). `primary_category` prefix (`politics.uk/us/world/europe`) is the
  deterministic fallback; `geo_region` is NOT used.
- **(b) One pass with per-region verdicts — NOT per-region pools.** The pass is one temp-0 Haiku
  call over the top-N, fingerprint-cached, at ~300 runs/day. Per-region pools = 3× LLM
  spend/latency + lost cross-region calibration. Keep one pass; the rubric asks for a
  region-relative verdict + a region tag.
- **(c) Per-region soft caps** (config `TOP_BLOCK_REGION_CAPS = {uk:4, us:3, world:2}`), applied
  in `cap_bulletin` after region-group ordering; keep `MAX_PROMOTIONS=15` as the global runaway
  backstop. Low urgency (max 8/run today, so it rarely binds).
- **(d) #155 re-stamp + #158 guard.** Add `top_story_region` to `ranked_stories` and a `region`
  column to `data.editorial_reviews`; `restamp_top_story` re-stamps both from a `{story_id:
  region}` map (still unconditional, gated on `run_reviewed`). `guard_top_block` is
  region-agnostic (dedups by entity+topcat, keeps higher merit) — no logic change; it should run
  ACROSS the whole region-ordered block so a UK+US pair on the same global event dedups.
- **(e) Migration is free via the re-stamp.** Add the columns NULL-default; the unconditional
  re-stamp tags every flag with a region within one review cycle (minutes). Treat NULL region as
  `world` until first re-review. No back-fill script, no downtime.

## Retire `FRONT_PAGE_REGION_LEAN`
Its only job is ordering the top block (score × 1.35 uk / 1.15 us / 1.10 other). Hard
region-group ordering (config `TOP_STORIES_REGION_ORDER`) replaces it entirely; within a region,
order falls back to `normalized_score` + the `country_weight` tiebreak. Retire `_front_page_lean`
and the multiplier branch in `_top_block_order`. Deliberate behaviour change: the region order
then also applies to the bare front page (UK-first) — matches "UK app leads with UK".

## Rubric change (editorial.py::build_system), tests, size
Add per-region verdict + a `"region":"uk|us|world"` output field (the stopgap already added the
region-relative BAR; the model adds the region TAG). Tests: `test_editorial` (region output +
region-relative cases), `test_selection`/`test_thresholds` (region-group ordering, per-region
caps, DELETE the lean-multiplier tests), ranked-list re-stamp region test, region-fallback test,
Alembic migration test. **Size: medium-large, ~2-3 focused days**, highest risk in `thresholds.py`
(the top-block ordering the demo shows). Bump `RUBRIC_VERSION` when the rubric gains the region tag.

## Separate follow-up — Farage twin-cluster
Farage's resignation exists as two clusters (240779 unflagged, 242537 flagged in 4/5 recent
runs); merit-ranking surfaces the unflagged twin. This is a **clustering** fragmentation issue,
independent of both the stopgap and the geographic model — the same real-world event forming two
`stories` rows. Investigate the anchor/hint keying that let the twin form (post-#156 the
demoted-entity hint path fragments deliberately; a person-entity like Farage should still
co-cluster). Not urgent for the demo; note for the clustering backlog.

---

# Top-stories: deferred pieces of the final spec

The final top-stories spec (region-toggle membership + backfill) shipped, with two items parked:

- **Middle East / Africa / Asia regional toggles.** Removed — the pipeline can't attribute subject
  region for them today (subject region is derived from the `politics.*` prefix, and there is no
  `politics.middle-east/africa/asia`). They were dead switches. Their stories now reach the top
  block via **WORLD** (the catch-all). Bring them back when the editorial pass **emits a region
  tag per flagged story** (the post-demo geographic model): then `_subject_region` reads the
  editor's tag instead of the category prefix, and the toggle set can grow. Removed from
  `TOP_STORIES_REGIONS`, `categories.yml`, and the `rules.yml` categorisation rules.
- **Per-user region order (drag-to-reorder).** The top block is presented in config order
  (`TOP_STORIES_REGION_ORDER = [uk, us, europe, world]`). `include_categories` is already an
  ordered list, so honouring the user's own toggle order is a one-line switch in `cap_bulletin`
  (order the region groups by first appearance in `include_categories` instead of the config).
  Deferred only because iOS has no reorder UI yet — add drag-to-reorder, then flip the backend.

Backfill interpretation (documented, in case it's revisited): "highest-ranked followed-topic
stories, in topic order" = membership by **rank** (the best topic stories fill the spare slots),
presentation by **topic (filter) order** — mirroring the top block's rank-membership /
group-presentation split.
