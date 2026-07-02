# LLM-primary categorisation

Rebuilds categorisation so the **LLM is the primary classifier** (not a fallback),
constrained to the taxonomy. Fixes the diagnosed ~1-in-6–8 miscategorisation
(context-blind keyword collisions like `"shares"`→`business.markets`, bare city-name
entity boosts, region-blind politics, pool blindly overriding content).

## Mechanism

- **Cluster-level, one Haiku call per story** (`cluster_categoriser.categorise_story`),
  in the categorise handler — replaces the keyword majority vote. Keyword majority
  survives only as a fallback when the LLM is disabled/unavailable.
- **Strictly constrained** to current taxonomy leaves (`llm_categoriser.classify_story`).
  The model must return a slug from the exact allowed list.
- **Pool hints, not deciders**: pool topic + country are passed as weak context; the
  content decides. Politics is region-aware (`politics.uk/us/europe`, else `politics.world`).
- **Valid-slug guard — CORRECTNESS OVER COVERAGE** (`guard_valid` + `offered_targets`):
  keep a story ONLY when its primary is a real leaf we offer, OR a top-level bucket it
  genuinely belongs to. Anything else — an off-taxonomy subject (ice hockey, crime, an
  unlisted region) or the model's `none` sentinel — is **DROPPED** (excluded). We do NOT
  reroute an unfitting slug up to a coarse ancestor: better to omit than mis-file. A DROP
  is distinct from an API failure — a drop excludes; only a failure falls back.
- **Persist where ranking reads it**: ranking uses the representative article's
  `normalisation_articles.category_primary` (candidate_loader), NOT
  `data.stories.primary_category`. So the resolved category is written to the article
  field (all members) as well as the story row; a DROP clears both (→ NULL).
- **Cached by content hash** (`data.story_categorisations`, mirrors `story_summaries`):
  a story is classified at most once per (content, model); re-cluster / replay / backfill
  reuse the cached leaf. Table is created lazily (`CREATE TABLE IF NOT EXISTS`); DDL also
  in `docs/sql/story_categorisations.sql`.

## Config

`enable_llm_primary_categorise` (default true). Off ⇒ legacy keyword majority vote.
Model = `settings.fallback_model` (Haiku).

## Cost (measured on prod sample)

~1290 input + ~55 output tokens/story ⇒ **~$0.0016/story** (Haiku $1/$5 per MTok).
Prompt caching does **not** apply — Haiku's cache minimum is 2048 tokens and the static
prefix is ~1230. Cost is controlled instead by the content-hash cache (classify once).
- Peak ~700 stories/day ⇒ ~$1.1/day; normal days (tens of stories) ⇒ a few pennies/day.
- One-time backfill: all 1460 stories ⇒ ~$2.30; invalid-only (~115) ⇒ ~$0.20.

## Backfill

`devtools/recategorise_stories.py` — re-categorises legacy/invalid-primary stories (or
`--all`). Dry-run by default; `--commit` persists. One-time migration.

## Known taxonomy gaps surfaced

The model wanted leaves that don't exist: ice hockey (`sport.nhl`), a `north-america`
region, women's/plural cricket drift, and there is no home for **crime / local
accidents**. Under correctness-over-coverage these are now **dropped**, not filed under a
coarse ancestor. If we want to cover any of them, add the leaf to the taxonomy.

## Follow-up (not in this PR)

A dropped story's category is cleared to NULL. Ranking's topic filters already ignore it,
but the front-page (top-stories) path scores by coverage without a category check, so a
high-coverage NULL-category story could still surface there. Recommend excluding
NULL/uncategorised candidates in `candidate_loader` (or the selection) for a fully clean
drop. Dropped crime/accident items are typically single-source, so this is a small
residual, not a live leak.
