# LLM editorial pass

The final-boss review over categorisation + ranking — an editor looking at the front page
before it goes out. Same spirit as the connective-tissue LLM, but it runs on the RANKED
candidate set (after cluster → categorise[#113] → rank, before selection).

## What it does (one Haiku call)

Reviews the top `REVIEW_N` (40) ranked candidates — the only stories that can surface even
in the deepest bulletin (18) — and returns BOUNDED corrections only:

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

~4100 in + ~290 out tokens ⇒ **~$0.0056/call** (Haiku $1/$5). Cached per ranking run, so cost
= (distinct ranking runs that produce a bulletin) × $0.0056 — a handful a day ⇒ pennies/day.

Config: `enable_editorial_pass` (default true).

## Verified on prod (read-only, real candidate set)

40 reviewed, 6 changed, all bounded:
| story | before | after |
|---|---|---|
| PlayStation reputation (high coverage) | #2 technology.gaming | #30, culture.tv-film, −1 (trivial demoted) |
| "How to get USA v Belgium tickets" | #18 | #40, −2 (listicle demoted) |
| Trump crypto $1bn | #1 politics.us | #25 business.companies, −1 (recategorised) |
| Microsoft AI company | #4 business | technology.companies (recategorised) |
| China ethnic-unity law (under-covered) | #26 | **#1** top-stories.asia, +1 (major promoted) |
| Russian attack kills 20+ in Kyiv | #33 politics.world | **#2** top-stories.europe, +1 (major promoted) |
