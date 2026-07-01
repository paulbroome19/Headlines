# Headlines — End-to-End Logic Audit

Read-only audit of the whole machine against the intended product: a **UK-first audio
news app for finance-commuter users**. Traced iOS + backend on `origin/main` (through
PR #82) and verified against **prod data** where it mattered. For each area: what the
code actually does vs what it should do, then every gap/contradiction/dead-code/
miscalibration, prioritised.

**Priority key:** 🔴 demo-blocking · 🟠 product-coherence · 🟡 polish.

---

## 0. Priority summary (read this first)

| # | Finding | Pri |
|---|---------|-----|
| 1 | **Reinstall / fresh device grabs an *arbitrary* backend profile** (`GET /data/profiles` returns ALL profiles unfiltered; iOS takes `.first`). On TestFlight with >1 tester, everyone shares/steals profiles. | 🔴 |
| 2 | **Score ceiling (7.34) < top-stories bar (7.5)** → the "clears the bar" path (`via_front`) *never fires*; the front page is 100 % #81 backfill. `short` preset bars (8.5 / 7.5) are entirely unreachable. | 🔴 |
| 3 | **Burst mode still ON** (`INGEST_BURST_MODE=true`) — idle at the 1005 cap now, but a redeploy on a new UTC day re-bursts. | 🔴 |
| 4 | **Edition replays heard/skipped stories:** (a) skip-early → `rejected`, which the edition doesn't drop → recurs same-day; (b) the first edition of a *new day* doesn't filter already-heard stories → yesterday's heard stories replay. | 🟠 |
| 5 | **Taxonomy slug mismatches:** `world`/geopolitics is produced + weighted but is **not a selectable picker category**; `entertainment.*` duplicates `culture.*`; `climate` vs `environment`. | 🟠 |
| 6 | **`business.markets` (1.70) outweighs `world`/`politics` (1.35)** — a minor market blip can edge a major world story on thin data. | 🟡 |
| 7 | Home greeting (iOS bands) ≠ audio greeting (backend bands); "GOOD NIGHT" reads like a sign-off. | 🟡 |
| 8 | **No user control over briefing length/preset** — `maxDurationMinutes` hardcoded to 5 (→ `medium`). | 🟡 |
| 9 | **12 dead Swift files** (old playback stack) + a second orphaned `BulletinPlayer`. | 🟡 |
| 10 | Dev reset paths don't clear `profileId` (orphan the old profile). | 🟡 |

---

## 1. Identity & lifecycle

**What it does.** A user is identified purely by iOS `@AppStorage("profileId")` (an
auto-increment backend id) + `userName` + `didOnboard`. `data.profiles` has **no device
identifier** column (`migration a1b2c3d4e5f6`); the API has **no per-user auth** — a
single shared app key only (`middleware/security.py`: "NOT an identity system").

**What it should do.** One account per device, keyed by an id that **survives reinstall**
(server-side, device-keyed).

**Gaps.**
- 🔴 **Reinstall does not remember the user — and worse, it hijacks a random profile.**
  `@AppStorage` is wiped on uninstall → `profileId = 0`. On launch,
  `HomeContainerView.startBriefing` (L43–50) does: *if `profileId <= 0`, fetch
  `ProfileService().fetchProfiles().first` and adopt it.* `GET /data/profiles`
  (`profiles.py`) returns **every profile in the DB, unfiltered**. So any fresh install /
  reinstall / new TestFlight tester **adopts an arbitrary existing profile** (often
  another user's). This is the single biggest lifecycle bug: profiles are effectively
  shared, not personal.
- To make reinstall *remember*: (iOS) read `UIDevice.current.identifierForVendor`, send
  it on create + on launch; (backend) add a nullable `device_id` column, `POST` upserts
  by it, `GET /data/profiles?device_id=…` filters to that device; (iOS launch) if the
  device has a profile, restore `profileId`/`userName`/topics and set `didOnboard=true`,
  else onboard. `identifierForVendor` survives reinstall as long as the vendor keeps ≥1
  app installed — good enough; a Keychain-stored UUID is the more durable option.
- **Dev reset.** Two DEBUG-only paths exist: `-resetFirstRun` launch arg
  (`HeadlinesApp.init`) and long-press the "P" on Home (`HomeView.debugResetFirstRun`).
  Both clear `userName` / `didOnboard` / `hasSeenHomeHint` → re-onboard. 🟡 Neither
  clears `profileId`, so the old backend profile is orphaned and a **new** one is
  created. Spec for a clean reset: also clear `profileId` + `selectedTopics`, and
  (ideally) `DELETE /data/profiles/{id}` or a `/dev/reset` that removes the device's
  profile + `user_story_state` so onboarding starts truly fresh. Keep DEBUG-gated.
- **Stale test profiles = evidence.** Prod profile 3 (`['world.uk','business.markets']`)
  is an old-taxonomy artifact (`world.uk` barely matches anything now) — direct evidence
  that profiles persist server-side while the app's taxonomy has moved on, and that
  there's no migration/cleanup of old profiles.

---

## 2. Onboarding & preferences

**What it does.** Name step (`CreateProfileView`) → filters step (`BuildBriefingView` →
shared `FiltersScreen`), default = **Top Stories only** (#79), now with a **back button**
to the name step (#83, this pass). Filters persist as leaf-id JSON + on the backend
profile's `include_categories`. The picker is built from `GET /data/categories` (current
taxonomy), so **new onboarding writes current-taxonomy slugs** ✓.

**Gaps — slug currency (verified against prod `category_primary`, last 36h).** The
categoriser (rules.yml + entities.yml + LLM fallback) mostly covers picker slugs, and
hierarchical prefix-matching means selecting `sport.football` catches
`sport.football.premier-league.*` ✓. **But there are real mismatches:**
- 🟠 **`world` / geopolitics is not selectable.** The taxonomy top-level is
  `top-stories, politics, business, technology, culture, science, health, environment,
  sport` — **no `world`**. Yet the categoriser produces `world.europe/asia/uk` and
  **`politics.world` (53 articles)**, and the ranker weights `world` at **1.35**. Net:
  a user filtering by topics **cannot select World/geopolitics** — those stories only
  surface via Top Stories. Either add a `world` picker category or route these into
  `politics.*`/`top-stories`.
- 🟠 **`entertainment.*` duplicates `culture.*`.** The categoriser emits both
  `culture.celebrity` (30) **and** `entertainment.celebrity` (5), `culture.tv-film` and
  `entertainment.tv-film`, etc. The picker offers **`culture`, not `entertainment`**, so
  `entertainment.*` stories are unreachable via the picker (and split the weighting:
  `culture` 0.40 vs `entertainment` 0.30). Pick one namespace.
- 🟡 **`climate` (1) orphan** vs the taxonomy's `environment` — doesn't match an
  `environment` filter.
- 🟡 `politics.world` (53) isn't a selectable leaf (`politics` children are uk/us/europe/
  global) but is caught by the `politics` parent, so it's only half-orphaned.
- **Old profiles** keep whatever they saved (e.g. `world.uk`); there's no reconciliation
  when the taxonomy changes.

---

## 3. The edition (what they hear)

**What it does.** #82 persists a per-`(profile, UK-local date, request_hash)` ordered
story set (`data.user_daily_editions`). First tap of the day = the fresh selection;
regenerations **reuse** it — drop heard stories, keep unheard in place, refill freed
slots, splice a new #1 lead. #81 guarantees it's never empty while stories exist. Heard
tracking works: iOS fires `completed`/`skipped`/`abandoned` events immediately to
`/data/bulletins/{id}/event` → `compute_new_state` → `consumed`. The reconcile is
stable (verified: a regenerate whose fresh set *dropped* stories still keeps the unheard
ones).

**Gaps.**
- 🟠 **Skip-early stories recur.** `skipped` with `position_pct < 0.5` → **`rejected`**
  (not `consumed`). The edition drops only `consumed`
  (`get_consumed_story_ids` → `WHERE state='consumed'`), so a story you **skipped early
  keeps coming back** in the same day's edition. (This is a behaviour change from the
  old `get_excluded_story_ids`, which excluded rejected too.) Fix: drop `consumed` **and**
  `rejected` from the edition.
- 🟠 **New-day edition replays heard stories.** `resolve_daily_edition`: when no edition
  exists yet (a new UTC/UK day), `final_ids = fresh_ids[:12]` with **no heard filter**.
  So a story heard yesterday but still inside the 36h window **reappears** in today's
  first edition. Fix: filter `heard` on the first-edition path too.
- **Otherwise composes correctly:** heard-everything → empty edition → "you're all
  caught up" (correct); dedup within a bulletin exists (`dedup_same_event` + seen-sets).

---

## 4. Ranking & categorisation (order + routing)

**What it does.** `importance = coverage · prominence · freshness · category_tiebreak ·
source_authority`, coverage-dominant. #81 recalibrated categories and added a
source-authority tiebreak; verified on prod that the 23-source PlayStation story leads,
politics clusters mid-page, and the single-source tail now sorts war/politics above
gaming/soap (the "Teams > Zelensky" absurdity is fixed). Depth-by-rank **works**: the
word target reaches the LLM prompt (`llm_summariser._DEPTH_SPEC`: lead ~120w → brief
~25w). Geo roll-up **works** (`pool_country → geo_region`, `via_region` match). Hierarchical
category matching **works** (prefix).

**Gaps.**
- 🔴 **Threshold calibration vs the score ceiling.** Live 36h **max normalized score =
  7.34**. Bars: `medium` top-stories **7.5** / category 6.0; `short` 8.5 / 7.5;
  `detailed` 6.0 / 4.5. So **0 stories clear the medium top-stories bar** — `via_front`
  (the "clears the universal bar") is **dead**; the entire front page is #81 backfill.
  On `short`, **both** bars (8.5, 7.5) are unreachable → topic filters would return
  empty (no backfill for topics). Only `medium`'s category bar (6.0, 12 stories clears)
  actually fires. **Recommend** recalibrating `IMPORTANCE_HALF` (currently 2.2, which
  compresses everything below ~7.4) and/or the bars so the biggest stories reach ~9–10
  and the bars mean something again. It "works" today only because backfill masks it.
- 🟡 **`business.markets` (1.70) > `world`/`politics.global` (1.35).** Intended
  finance-audience lean, but on thin single-source data the category tiebreak
  (+14 % vs +7 %) can lift a **minor market story above a major world story** when
  coverage is equal. Not the old absurdity (soft news is now firmly demoted: gaming 0.40,
  culture 0.40, entertainment 0.30, sport 0.55), but a "FTSE flat" leading over a war
  story is a plausible edge case on a quiet, thin-data day. Coverage resolves it once
  the DB fills. Consider whether markets *should* outweigh world for a general morning
  brief.
- **Other weight combos checked — no remaining absurdities:** health 1.10 / environment
  1.10 / science 1.00 are sensible; `technology.ai` 1.15 sits below world/politics;
  `technology.companies` 0.95 and `technology.gaming` 0.40 are correctly below hard news.
- **Routing** is otherwise sound, modulo the §2 slug mismatches (`world`/`entertainment`/
  `climate`).

---

## 5. The listen (playback)

**Verdict: coherent.** The live path `HeadlinesApp(.home) → HomeContainerView →
NowPlayingView(player: BulletinPlayer)` holds end-to-end:
- Loader shows on `.idle/.loadingManifest/.preparing` (Solari + continuous creep),
  dismissed when audio starts; readiness gate waits for intro + first-story so the
  greeting is never skipped.
- Greeting (intro) plays first; stories in ranked manifest order; SOURCES row wired.
- Play/pause icon reflects `playerState`; `updateNowPlaying()` fires on every state
  change (lock screen correct); `MPRemoteCommandCenter` wired; skip/skipBack/tracklist
  set `.playing` directly. The recent loader/play-pause/empty-state/tracklist fixes
  compose without contradiction; the `playerState` switch is exhaustive.

**Gaps.**
- 🟡 **12 dead Swift files** (the superseded pre-BulletinPlayer stack): `PlaybackView`,
  `PlaybackViewModel`, `BriefingViewModel` (which also creates a **second, orphaned
  `BulletinPlayer()`**), `AudioPlayer`, `PlayerView`, `ProfileListView`,
  `BriefingLoadingView`, `BriefingErrorView`, `LoadingStateView`, `ErrorStateView`,
  `MarqueeText`, `PlayingBarsView`. None are reachable from `HeadlinesApp`. Recommend
  deleting to shrink surface (and remove the confusing second player instance).

---

## 6. Anything else (proactive)

- 🔴 **Burst mode still on.** `INGEST_BURST_MODE=true` on Railway. It hit the 1005/day
  cap and is idle, but leaving it on means a redeploy on a new UTC day re-bursts (3×
  budget). Turn it off now that the DB is seeded.
- 🟡 **Greeting inconsistency.** iOS `TimeOfDay` bands (morning 5–11, afternoon 12–17,
  evening 18–21, night 22–04) differ from the backend audio greeting
  (`connective._time_of_day`: morning 0–11, afternoon 12–16, evening 17–21, night 22–23).
  So the **home-screen greeting and the spoken greeting disagree at 00:00–04:59 and
  17:00–17:59**. Also "GOOD NIGHT" (22:00–04:59) reads like a sign-off, not a news
  greeting — consider "GOOD EVENING"/a neutral header for late hours.
- 🟡 **No briefing-length control (depth vs preset).**
  - *(a) Depth-by-rank* — **applied** (lead 120w … brief 25w in the LLM prompt). ✓
  - *(b) The short/medium/detailed preset* (the real length/threshold control, #67)
    **has no UI.** `maxDurationMinutes` is **hardcoded to 5** in `BuildBriefingView`
    (onboarding) and `FiltersScreen` (settings create), so `_preset_from_minutes` always
    returns **`medium`**. The user **cannot choose their briefing length** even though the
    backend supports three presets. To expose: a control in onboarding/settings that sets
    `max_duration_minutes` (e.g. 3 → short, 5 → medium, 8 → detailed) and persists it on
    the profile (the profile already carries `maxDurationMinutes`; settings-update already
    reads `profile.maxDurationMinutes`). **Caveat:** fix the §4 calibration first — `short`
    currently returns nothing (bars unreachable), so exposing it as-is would give an empty
    "short" briefing.
- **Edge cases checked:**
  - *Brand-new user before the DB fills* → genuinely empty → "you're all caught up"
    (correct); the burst has masked this, and normal tiers should keep it filled
    post-burst.
  - *User who has heard everything (36h window)* → empty edition → "all caught up"
    (correct) — but see §3(b): the next day it replays heard stories.
  - *Filter with zero content* → with #81, Top-Stories/flag briefings backfill and are
    never empty; a **topic-only** filter that genuinely matches nothing still shows the
    graceful empty state (intended).
  - *Duplicate stories* → within-bulletin dedup exists; across editions the edition
    dedups by story_id.

---

## Recommended order of attack

1. 🔴 **Identity** — device-keyed profiles (stop the arbitrary-profile hijack) before any
   multi-tester TestFlight round. Biggest correctness risk.
2. 🔴 **Turn off burst mode.**
3. 🔴/🟠 **Ranking calibration** — recalibrate `IMPORTANCE_HALF`/bars so thresholds fire
   and `short`/`detailed` are viable; this also unblocks a length control.
4. 🟠 **Edition heard-filter** — drop `rejected` too + filter heard on the first-of-day
   edition.
5. 🟠 **Taxonomy** — resolve `world` (add as a category or reroute), and the
   `entertainment`/`culture` duplication.
6. 🟡 Polish — greeting bands, length UI, dead-code deletion, dev-reset completeness,
   `business.markets` vs `world` weighting.

_Audit is read-only; no code changed except Part A (the onboarding back button, PR #83)._
