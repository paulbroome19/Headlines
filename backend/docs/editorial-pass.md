# LLM editorial pass

The final-boss review over categorisation + ranking — an editor looking at the front page
before it goes out. Same spirit as the connective-tissue LLM, but it runs on the RANKED
candidate set (after cluster → categorise[#113] → rank, before selection).

## What it reviews — laddered by tier (not a flat top-N)

Review DEPTH is scoped by newspaper tier so attention/cost concentrates where it matters:

| Tier | Categories | Depth | Grouped by |
|---|---|---|---|
| 1 | top-stories, politics, business | top ~50 each | top-level |
| 2 | world, health, science, environment | top ~20 each | top-level |
| 3 / niche | technology, sport, culture (a specific club, …) | top ~5 each | leaf |
| — | uncategorised (major hard news #113 left homeless) | top ~20 | — |

Plus a **floor guarantee**: every distinct leaf contributes its top-2, so a story that can
reach a bulletin via a selected category's guaranteed floor is never skipped even if it
falls below its tier's depth. Total ≈ 320–400 stories (scales with the day's category
spread). If that exceeds `CALL_BATCH` (200) it splits into tier **bands** — front page
(tier 1 + uncategorised) first — so each Haiku call attends carefully rather than skimming.

Per reviewed story it returns BOUNDED corrections only:

- **Category** — fix a clearly-wrong category (belt-and-braces over #113).
- **Significance** — nudge importance UP/DOWN by at most **±2.0** on the 0–10 axis, where the
  coverage-based rank is obviously wrong (a 27-source PlayStation reissue pulled down; a
  major but thinly-covered story pushed up). The clamp means it *adjusts*, never overrides.
- **Top story** — promote to the front page (category → `top-stories.<region>`) a story that
  is a major overlap of the topic categories OR significant hard news with no topic home
  (a major disaster/crime/accident); demote a trivial one off it. Top-stories is a
  significance judgment, not a catch-all bin.

## Guardrails (reviews and corrects, does not re-rank)

- Significance clamped to ±`SIG_CLAMP` (2.0).
- Category constrained to the SAME valid targets as #113 (`guard_valid`) — junk is dropped.
- Net new front-page promotions capped at `MAX_PROMOTIONS` (6) — no wholesale reshaping.
- Only stories the editor changes are returned; everything else keeps the pipeline's rank.
- Any failure → candidates used unchanged (purely additive).

## Guarantee (#113)

Pipeline order is `cluster → categorise (#113) → rank → select`. The editorial pass runs at
select-time, so every candidate it reviews already carries a #113 LLM category. The editor
is a second review on top, never the first categorisation.

## Where / caching

Runs inside `_threshold_select_with_depth`, between scoring and `select_by_thresholds`, so a
promoted story can enter the bulletin and a demoted one can fall out. Cached per
`ranking_run_id` (`data.editorial_reviews`, lazy `CREATE TABLE IF NOT EXISTS`): one call per
ranking run, reused across every user, filter and regeneration.

## Cost (measured on prod)

323 reviewed over 2 tier-band calls ⇒ ~25.2k in + ~1.0k out tokens ⇒ **~$0.030/run** (Haiku
$1/$5). Cached per ranking run, so cost = (distinct ranking runs that produce a bulletin) ×
$0.03 — a handful a day ⇒ pennies/day.

Config: `enable_editorial_pass` (default true).

## Verified on prod (read-only, real candidate set)

1131 candidates → 323 reviewed (T1 151 / T2 60 / T3 112), 2 calls, 15 changed, all bounded:
| story | before | after |
|---|---|---|
| "Spain v Austria LIVE" match-blog (front page!) | #33 top-stories.europe | **#1130**, sport.football.internationals, −2 |
| PlayStation reputation (high coverage) | #2 technology.gaming | #25, −1 (opinion, not hard news) |
| Taylor Swift donation (gossip) | #16 culture.celebrity | #91, −1 |
| Microsoft AI company | #4 business | business.companies, −1 (recategorised) |
| Hospital fire, fatalities (under-covered) | #29 top-stories.europe | **#2**, +1 (major, kept top story) |
| China ethnic-unity law | #26 top-stories.asia | #21 **politics.world** (demoted off front page) |
| Tucker Carlson (bad region) | #78 top-stories.middle-east | politics.us (recategorised) |
