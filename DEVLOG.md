# Development log

This is a running, session-by-session development log — the build's
working notes, not a polished document. Most of the implementation was
done with AI coding agents under my direction (see the README's "Notes"
section), and these entries capture the decisions, dead ends, and
fixes from each session: what changed, why, and what was next. It is
kept for provenance and as a record of the reasoning behind the code,
and reads newest-context-first within each session rather than top to
bottom.

## Changes Made This Session (2026-07-11 — LOAD-PATH PR L-D: the tap pins the selection)

**PR L-D of the load series. Backend + iOS. Stacked on L-C — ONE archive covers both.**

- Server (`data.py`/`profiles.py`/`selector.py`): home-preview returns `selection_id` + `lead_story_id`;
  `ManifestRequest.selection_id` (optional) → `prepare_bulletin_for_manifest` probes the cache with
  the tapped run (`parse_selection_run`) so a newer run landing between render and tap CAN'T swap the
  briefing; `/event` + `/summary` accept `selection_id` and `assert_event_selection` 409s a stale one
  (completes L-B's thread through events). All optional → older clients behave as today.
- Client (`BulletinManifest.swift`/`BulletinPlayer.swift`/`HomeContainerView.swift`): decode
  `selection_id`/`lead_story_id` on preview/manifest/readiness; `load(profileId:selectionId:)` carries
  the preview's selection_id on the manifest POST; `_selectionId` (from the served manifest) is echoed
  on every `/event` + `/summary` flush.
- Tests: backend 126 (+parse_selection_run, +stale-event 409), iOS 25 (+testFlushedSummaryCarriesSelectionId).

L-series complete pending the combined device archive (L-C + L-D) and deploy-verify.

## Changes Made This Session (2026-07-11 — LOAD-PATH PR L-C: the gate invariant, both sides)

**PR L-C of the load series. Backend + iOS.** Never green-light audio whose opener isn't the committed lead.

- Server (`data.py`): `/readiness` takes optional `profile_id`; `gate_opener_ok(opener, committed_lead)`
  pure helper; when profile_id is present, `safe_to_start` is BLOCKED if the first story segment
  (opener) != the edition's committed lead (story_ids[0]) — loud warning, never green-lights wrong
  audio. Backward compatible (no profile_id → today). `test_gate_invariant.py` (4, incl. 240779≠415760).
- Client (`BulletinPlayer.swift`): passes `?profile_id=` on both readiness polls (server can enforce);
  before starting playback, `openerMatchesQueueLead()` asserts firstAudibleStoryId == queue.items[0];
  mismatch → HOLD (don't play) + loud `gateLog` on the `com.headlines.flush` subsystem (category
  "gate") + surface `.failed` rather than hang. `testGateHoldsWhenOpenerDoesNotMatchBoardLead`.
- Tests: backend 123, iOS 24.

L-D (client pins the tapped selection + events echo selection_id) is stacked next on the same iOS
lineage so ONE archive covers L-C + L-D, verified in a single device session.

## Changes Made This Session (2026-07-11 — LOAD-PATH PR L-B: selection_id threaded + asserted)

**PR L-B of the load series. Backend, deploy-gated.** One selection identity threaded through the
load artifacts + a LOUD serve-time guard, covering the warmer path per Paul's rider.

- `selector.py`: `selection_token(run, hash)` = the selection identity `"{run}:{hash}"` — WHICH
  materialised selection an artifact belongs to. Stable across a heard/skip reconcile (changes
  story_ids, not run/hash), so it flags genuine run drift / cross-selection, not normal churn.
- `edition.py`: `get_ranking_run(...)` — the edition's PINNED run (None when no edition yet).
- `data.py`: `assert_same_selection(served_run, pinned_run, ...)` — a served bulletin MUST belong to
  the edition's pinned selection; a run mismatch RAISES loudly. Wired into `_get_or_assemble_bulletin`'s
  cached/warm serve branch (**the rider** — a warm bulletin whose run drifted from the pinned edition
  fails loudly, never a silent grandfather clause; no-op at create time when no edition exists).
- `selection_id` now carried on every load artifact: readiness response, both manifest returns
  (streaming skeleton + cached full), via the prep/plan/assemble dicts.
- Tests: `test_selection_id.py` (4) — token format, matching pass, no-edition no-op, cross-selection
  loud raise. Backend suite 119 pass.

Client carry + echo of `selection_id` on events (so a stale-selection event fails loudly too) is
**L-D** (client pins the tapped selection). Next: L-C (gate: first audible == queue.items[0], both
sides), then L-D. Verify L-B live post-deploy: a cross-run warm serve must 500, not serve.

## Changes Made This Session (2026-07-10 — LOAD-PATH audit + structural fix PR L-A: materialise-once)

Load-path audit (home render → first audio), analogous to the playback A–F audit. **Convicted the
08:25–08:32 BST (07:25–07:32 UTC) board≠audio incident from prod:** crossing D — a *within-run*
reorder. Run 7418, one request_hash; the start-pack synthesised the opener AUDIO for lead 240779
(Farage) from the pre-final skeleton order, then a second, independent dedup during background
assembly reordered the lead to 415760 (UK heat). The old code only logged `LEAD SHIFTED … should be
impossible` and served anyway — guarded, not structural. Root: the running order was (re)deduped at
multiple stages that disagreed (preview's LLM dedup FAILED → deterministic backstop; final succeeded).

**PR L-A of the load series (L-A→L-D). Backend, deploy-gated.**
- `run_background_assembly` (`data.py`): resolve the ONE authoritative (deduped, materialised) order
  UP FRONT; derive BOTH the start-pack opener AND the final assembly from it. Removed the second
  `resolve_materialised_selection`/`dedup_same_event` pass. Opener == final by construction.
- New pure `assert_opener_is_final_lead(...)`: the opener MUST be the final lead — a LOUD raise
  (fails the assembly), replacing the warning-that-served. `test_load_invariant.py` (4).
- `prepare_bulletin_for_manifest` (`data.py`): the streaming manifest now stamps the bulletin with
  the EDITION's PINNED run (resolved before the cache probe), not `get_latest()` — so bulletin,
  edition and preview share one run (closes crossing A on the incident path).
- Backend suite: 115 pass (+ the 4 invariant tests).

**Flagged (your call — not done here):** `_get_or_assemble_bulletin:775` still uses `get_latest()`;
it's entangled with the full run object and mainly serves the create-time warmer (no edition yet →
`latest` is correct), so it's higher-risk/lower-value. Next: L-B (selection_id threaded + asserted),
L-C (gate: first audible == queue.items[0]), L-D (client pins the tapped selection). Verify L-A via a
live re-trace of the cold-play/dedup-disagreement scenario after deploy — opener must equal final lead.

## Changes Made This Session (2026-07-10 — Playback migration PR F: single canonical PlaybackQueue)

**PR F of 6 — the capstone.** Extracts the single canonical running order the UI consumes as ONE object.

- New `PlaybackQueue` / `QueueItem` (in `BulletinManifest.swift` — no pbxproj change). Each `QueueItem`
  carries story IDENTITY + fully-resolved row state (isPlaying/isConsumed/isReady/isBuffering/isSynthesising).
- `BulletinPlayer.queue` projects the published order + resolved state into that one identity-keyed
  list — the player OWNS it. Recomputed from @Published inputs, always fresh.
- `NowPlayingView` tracklist now renders `player.queue.items` and reads NOTHING else — `trackRow`
  consumes only the item (was: re-deriving each row from ~6 scattered player properties —
  currentStoryId/pendingTapStoryId/synthesisingStoryId/consumedStoryHashes/isUnitReady/storyUnits).
- Tests (23/23): `testQueueProjectsOrderAndExactlyOnePlayingRow`,
  `testQueueBufferingAndOrderFollowIdentityAcrossReorder`.

**Exit criterion met** — the audit's Part-1 seam re-run shows ZERO index-addressed cross-boundary
paths and ZERO unguarded reconcile races (A tap-identity, B published-order+identity-highlights,
C one-cursor/one-instance, D identity-merge+guard, E authoritative-order+payload-bridges, F single
queue). Full migration A→F complete.

## Changes Made This Session (2026-07-10 — Playback migration PR E: versioned/authoritative readiness + payload bridge bindings)

**PR E of 6** — the two-sided, deploy-gated step (backend deploys on merge; client ships in a build).
Retires the last reconcile-internal index dependency (D's flag) and makes reconcile authoritative, not inferred.

**Backend (`data.py`, additive → old client still decodes):**
- `/readiness` transition segments now carry `prev_story_id`/`next_story_id` (the bridge pair).
- `/readiness` response carries `story_order` (authoritative deduped ordered story_ids — new
  `authoritative_story_order()` pure helper, unit-tested) and `provisional` (True while the pre-dedup
  skeleton is still in place). Backend tests: 111 pass incl. new `test_readiness_order.py` (3).

**Client (`BulletinManifest.swift`, `BulletinPlayer.swift`, optional fields → deploy-order safe):**
- `ReadinessSegment` gains `prevStoryId`/`nextStoryId`; `BulletinReadiness` gains `storyOrder`/`provisional`.
- `reconcileEditionIfShrunk` is now driven by the authoritative `story_order` (an explicit identity
  diff — reconcile whenever it differs from the current order, self-limiting), falling back to the
  legacy shrink heuristic only when `story_order` is absent (older backend).
- Rebuilt transitions take the bridge pair from the PAYLOAD (`rs.prevStoryId/nextStoryId`); the
  bindBy-keyed-by-segment-index path is now a fallback only — retires the D-flagged index dependency.
- Tests (21/21): `testReconcileUsesAuthoritativeOrderAndPayloadBridgeBinding`,
  `testReconcileFallsBackToShrinkHeuristicWhenNoAuthoritativeOrder` + `_testSegmentBinding` seam.

**Deploy/verify:** backend must deploy (Railway, on merge) BEFORE a client build benefits; both
sides are backward-compatible so either order is safe. Live-verify the readiness payload (new fields)
against prod after deploy.

## Changes Made This Session (2026-07-10 — Playback migration PR D: reconcile identity-diff + serialization guard)

**PR D of 6** — the substantive one. Closes the last two exit-criteria items on the readiness path.

- **Seam β (identity merge):** `applyReadiness` now matches segments by their stable backend
  `.index` via `firstIndex(where:)`, not the `segments[rs.index]` array subscript. After a dedup
  reconcile leaves a NON-contiguous index set, the old subscript misaligned (wrong slot / bounds-
  dropped) so a story's url was silently never filled. Position-independent now.
- **Serialization guard:** the playback poll captures `navGeneration` BEFORE the network fetch and
  applies via new `applyReadinessIfCurrent(_:fetchGen:)`; if a queue-rebuilding gesture
  (seek/skip/restart/stop — each bumps navGeneration) landed mid-fetch, the stale snapshot is
  DROPPED (next poll re-fetches ~1s later). No poll-driven reconcile/merge can clobber a
  just-happened navigation. Pending taps don't bump navGeneration and are identity-resolved → unaffected.
- **Watchdog / 45s ceiling re-verified:** the client HOLD ceiling (`holdCeilingSeconds = 45`) is
  time-based, checked on each applied poll — the guard defers a check by ≤1s and never resets
  `holdStartedAt`, so it's intact. The backend stall-watchdog REAP (`state == "failed"`) still
  records into `failedSegmentIdx` by backend index so enqueue skips it (pinned by test).
- **Folded in:** deleted the orphaned `Core/AudioPlayer.swift` (+ pbxproj refs).

Tests (19/19): `testReadinessMergeMatchesBySegmentIndexNotArrayPosition`,
`testStaleReadinessDroppedWhenGestureRaces`, `testFailedReadinessSegmentIsRecordedForSkip` + new
readiness-builder/explicit-index seams.

**Flagged (needs E, not deferred silently):** `reconcileEditionIfShrunk` still preserves transition
bridge bindings keyed by segment `.index` (`bindBy`, the code's own UNVERIFIED note) because
`ReadinessSegment` doesn't carry `prev_story_id`/`next_story_id`. Correct while readiness keeps
indices stable (the common case); a true renumber needs the readiness payload to carry the bridge
pair — that's exactly E (readiness carries the authoritative ordered storyId list + bindings). D
guards the race and kills the index-addressed MERGE; the bridge-binding identity move lands in E.

## Changes Made This Session (2026-07-10 — Playback migration PR C: one cursor, one player instance)

**PR C of 6.** Removes the structural dual-cursor and the dead second `BulletinPlayer` (audit §1.1/§1.3).

- **One cursor:** `storyIndex` DELETED from `BulletinPlayer` — it had zero internal reads (write-only)
  and its only external reader was the dead `BriefingViewModel`. `currentUnitIndex` is now the single
  story cursor; every current-story surface derives from it via `currentStoryId`. Removes the latent
  "board title ≠ audio during a bridge" divergence (`storyIndex` used to lag on transitions).
- **One instance:** deleted `BriefingViewModel.swift` (owned a second, never-instantiated
  `BulletinPlayer()`) and `PlayerView.swift` (the dead positional `seekToStory(at: Int)` tap path I'd
  flagged to defer). Both were never constructed anywhere. pbxproj references stripped (8 lines).
- Tests (16/16): new `testSingleCursorDrivesCurrentStory`; all prior green.

**Flagged (out of C's named scope — your call):** `Core/AudioPlayer.swift` is now fully orphaned
(never constructed; its doc-comment "Owned by BriefingViewModel" is stale). Left in place; say the
word and I'll remove it (it's a clean dead-code deletion).

## Changes Made This Session (2026-07-10 — Playback migration PR B: published atomic snapshot + identity highlights)

**PR B of 6** (stacked on A). Symptom class killed: the on-screen list / now-playing highlight /
buffering + synthesising spinners attaching to the WRONG row during a dedup renumber (audit §1.3
seam α — a derived order re-rendered non-atomically, highlights compared by offset).

`ios/.../Core/BulletinPlayer.swift`:
- `storyUnits` is now the **published** store (`@Published private(set)`), replacing the private
  `_storyUnits` + plain accessor (global rename, 44 refs). Every rebuild re-renders all surfaces
  atomically from one snapshot.
- `pendingTapStoryId` is now `@Published` (spinner appears the instant a pending row is tapped);
  the derived `bufferingUnitIndex` Int cache + `refreshBufferingIndex()` are **removed** (identity
  is the source now).
- New identity highlight sources: `currentStoryId`, `synthesisingStoryId` (buffering uses
  `pendingTapStoryId` directly).

`ios/.../Models/BulletinManifest.swift`: `StoryUnit.identity` — stable diffing key = story id.

`ios/.../Features/Playback/NowPlayingView.swift`: `ForEach(..., id: \.element.identity)`; row
`isCurrent`/`buffering`/synthesising compared by **story identity**, never the row offset.

Tests (15/15): new `testCurrentHighlightResolvesByIdentityAfterReorder`; the two pending tests
updated to identity assertions; `_testReorderStories` now re-anchors `currentUnitIndex` by identity
to mirror the real reconcile. Needs build for on-device verify.

## Changes Made This Session (2026-07-10 — Playback migration PR A: identity across the tap boundary)

Full playback-seam migration A→F underway (see the architectural audit). This is **PR A of 6**.

Symptom class killed: "tap a story → a DIFFERENT story plays" and the pending-tap variant, when a
dedup reconcile renumbers `_storyUnits` between render and tap (audit §1.4 seams #1, #2).

`ios/.../Core/BulletinPlayer.swift`:
- Durable pending pointer is now **story identity**: `pendingTapUnitIndex: Int?` → `pendingTapStoryId: String?`.
  Every consumer (`fulfilPendingTapIfReady`, `reassertPendingPriority`, `synthesisingUnitIndex`,
  the `seekToStoryUnit` pending branch, teardown) resolves it to the CURRENT row live. Fulfil drops
  the intent if the story left the order (never plays a neighbour).
- `bufferingUnitIndex` (@Published, UI spinner) is now a derived cache of `pendingTapStoryId`,
  re-resolved in a new `refreshBufferingIndex()` called on every pending change AND at the end of
  `rebuildTimeline()` — so the spinner follows the story across a renumber.
- New identity tap entry points: `playStory(id:)` and `seekToStory(id:)` resolve id→current index
  at call time.

`ios/.../Features/Playback/NowPlayingView.swift`: the tracklist tap now calls
`player.playStory(id: unit.storyId)` instead of `playStoryUnit(at: idx)`.

Tests (gesture-style, 14/14): `testTapPlaysStoryByIdentityNotStaleOffset`,
`testPendingTapPointerFollowsStoryAcrossReorder`, `testPendingTapDroppedWhenStoryRemovedByReconcile`
+ new `_testConfigureStories`/`_testReorderStories` seams. Highlight comparisons in the row are still
positional — that's PR B (ForEach by storyId + identity highlights). Needs build for on-device verify.

## Addendum (2026-07-08 — outcome-semantics verification: mapping confirmed, two drift bugs found + fixed)

Verified the action→state mapping end-to-end (not just emission).

**Mapping (authoritative = `compute_new_state`, user_story_state_repo.py) — matches spec:**
`completed → consumed`; `skipped → consumed at ANY position`; `abandoned → consumed iff
position_pct ≥ 0.5 (inclusive) else None (stays queued, replayable after 24h TTL)`.

**Flagged drift #1 (doc):** `record_bulletin_event`'s docstring (data.py) described `skipped <0.5 →
rejected` and `≥0.5 → consumed` — a `rejected` state the code no longer produces. Stale vs the
actual `compute_new_state` (skip → consumed at any position). Fixed the docstring to match the
verified code and noted 'rejected' was retired. (Flagged, not silently aligned — the operative
mapping already matched spec; only the comment was wrong.)

**Flagged drift #2 (real bug, client) — abandon-during-bridge false-consume:** `currentPositionPct`
only advances while a STORY segment is live (handlePeriodicTime's isStory guard), so it goes STALE
during the bridge/intro LEADING INTO the next story, holding the PREVIOUS story's value. My earlier
Fix 2 (anchor abandoned on currentStoryUnit) would then flush `abandoned` for the upcoming story B
carrying story A's stale pct (e.g. 0.95) → server maps `abandoned ≥0.5 → consumed` → B falsely
consumed though it never played. Fixed: the abandoned event reports position 0 unless the story's
OWN audio is the live item (`currentSegment?.isStory == true`). Skip position is cosmetic (skip
ignores position), so left as-is.

**Position semantics per gesture (confirmed):** skip → departing story gets currentPositionPct,
intervening stories 0 (irrelevant — skip ignores position); completed → hardcoded 1.0; abandoned →
currentPositionPct only when the story's own segment is live, else 0. currentPositionPct is
fraction through the current STORY SEGMENT (per-segment; for a split lead opener+remainder it
resets at the remainder boundary — minor under-measure near remainder start, pre-existing, within
tolerance for a 50% heuristic).

**Tests:** backend `test_outcome_mapping_boundaries` (skip@0→consumed, abandon@0.49→queued,
abandon@0.5 & 0.51→consumed, complete→consumed, unknown→no-op) — 5/5. iOS
`testCloseWhileBridgePlayingRecordsAbandoned` now asserts position 0 despite a seeded stale 0.95 —
11/11.

## Changes Made This Session (2026-07-08 — Next-button skips silently dropped: skip emission was gated on the LIVE segment, not the logical story)

### The bug (build 47 + 48, two fresh profiles, zero events server-side)

Reproductions: skip a story mid-synthesis via the ▶| NEXT button → back → refresh → story still
present. Server-side reads for both windows showed **zero `/event` POST and zero `/summary` POST**
from the session, and the story's `user_story_state` row stayed `queued` (untouched). So the skip
never reached the server on EITHER delivery path.

Root cause — NOT a missing/separate handler. The next button (and lock-screen next) both funnel
through `skip()` → `seekToStoryUnit`, the same primitive #190 touched. But the skip was recorded
off the **raw `currentSegment.isStory`** gate. When Next is pressed while a BRIDGE/transition
segment is the live item — or during a nil buffering gap mid-synthesis — that gate is false, so
`seekToStoryUnit` recorded nothing. Both delivery paths (`fireEvent` live `/event`, and the
accumulate → `/summary` batch) carry the SAME event object, so a dropped record kills both at once.
The drain-time `abandoned` append had the identical `currentSegment.isStory` gate, so closing while
a bridge played dropped that too. #190 only fixed the pending-early-return ORDERING of that same
gated block — it never removed the `isStory` requirement, so the mid-synthesis next-button case was
never covered.

### Fix — anchor event emission on the LOGICAL current story, not the live segment

`ios/.../Core/BulletinPlayer.swift`:
- `seekToStoryUnit` skip-record block: keyed off `currentUnitIndex` (the story the playhead
  occupies, which "stays the current story even during its bridge/intro"), not `currentSegment`.
  Now records `skipped` for EVERY story a forward move clears (multi-story scrubber jumps included),
  deduped via `_skippedStoryHashes`, suppressed for already-completed stories. Still runs before the
  pending-tap early return (keeps #190).
- `drainSummary` abandoned append: anchored on `currentStoryUnit` instead of `currentSegment`, so a
  close while a bridge plays still records the in-progress story; suppressed if already skipped/completed.

### Tests (`ios/.../Tests/HeadlinesTests.swift`) — one per gesture path, all green

- `testSkipWhileBridgePlayingStillRecordsSkip` — the build-47/48 gap (Next while a bridge is live).
- `testForwardJumpPastMultipleStoriesRecordsEachSkip` — scrubber clears several stories.
- `testCloseWhileBridgePlayingRecordsAbandoned` — exit-path counterpart.
- Existing `testSkipToPendingStoryStillRecordsSkip` (#190) still passes. New `_testConfigureForwardSkip`
  seam. `** TEST SUCCEEDED **`, 11/11.

### State / next

Needs a **build 49** (iOS client change — rebuild + reinstall). None of the last three days of
skip-testing could have exercised this path in the mid-synthesis state: the working code path did
not exist until this fix. (The `consumed` rows that DID appear were natural-completion `completed`
events, not skips.) Next: build 49, repro the same next-button-mid-synthesis skip, confirm a
`/event` (or `/summary`) POST lands and Farage's row leaves `queued`.

## Changes Made This Session (2026-06-30 — Settings filters: top-left back chevron instead of a bottom bar)

### iOS — Differentiate the two filter contexts' navigation chrome

The shared `FiltersScreen` previously used the bottom `BottomActionBar` in BOTH
contexts ("COMPLETE SETUP" / "DONE"). Settings is a detour, not a forward step,
so it now uses a back chevron instead of a destination footer. The shared tree,
light styling, toggles and partial/cascade logic are UNTOUCHED — only the
navigation chrome is parameterised.

**`Features/Filters/FiltersScreen.swift`**
- Replaced `ctaLabel` + `showStepIndicator` with a `Chrome` enum:
  - `.onboarding(ctaLabel:)` — UNCHANGED behaviour: bottom action bar
    ("COMPLETE SETUP" + machined arrow) + "2 OF 2".
  - `.settings(onClose:)` — top-left back chevron (matches the playback screen's
    `chevron.left`, 18pt semibold ink, 40×40), NO bottom bar, tree fills the
    screen.
- Save-on-back: tapping back when the tree is ready runs `finish()` → the SAME
  `onComplete` (updateProfile + persist JSON + dismiss) the old "DONE" button
  used; if still loading/failed it routes through `onClose` (plain return). The
  chevron shows a spinner while saving and surfaces any save error inline.
- `ProfileFiltersView` passes `.settings(onClose: { dismiss() })`;
  `BuildBriefingView` passes `.onboarding(ctaLabel: "COMPLETE SETUP")`.

### State
- Builds with 0 errors. Both committed Home UI tests still pass. Verified on sim:
  settings (from "P") shows the top-left back chevron, no bottom bar, full-width
  tree; onboarding still shows "2 OF 2" + "COMPLETE SETUP" + arrow unchanged. A
  throwaway prod UI test confirmed save-on-back end-to-end — toggle → Back →
  returns Home (only happens if `updateProfile` succeeded) → reopen shows the
  tree (round trip). (Throwaway test not committed; needs the live backend.)

## Changes Made This Session (2026-06-30 — Home rebuilt in the LIGHT register (C6) + cleanup)

### Cleanup (committed separately, before the home work)
- Removed superseded dark profile UI: `ProfileSheet` (private, in
  HomeContainerView), `ProfileFormView.swift`, and the already-dead
  `BriefingView.swift` (its only remaining consumer; never instantiated).
- Broadened fresh-install onboarding defaults from World + Business to
  **World, UK, Business, Technology** (`FiltersScreen`'s
  `onboardingDefaultSelection`) so a no-tap user gets a fuller first briefing.

### iOS — Home rebuilt to the locked light C6 design

Replaced the old dark full-bleed Home (two-tone board + centred play disc) with
the light register: a near-white page, the dark flap-board greeting as a
contained hero, and the machined arrow docked into its base. Now matches
onboarding + playback — one product.

**`Shared/Theme/Board.swift`** — added a shared `.boardCard()` View modifier
(charcoal gradient + grain + hairline highlight + soft shadow) so the Home hero
card and the onboarding cards are the identical contained instrument.
`CreateProfileView` refactored to use it (no visual change).

**`Features/Home/HomeView.swift`** — full rewrite to C6:
- Masthead: "HEADLINES" wordmark (`Font.label`, ink caps) top-left; quiet
  refresh (↻) + the "P" profile circle top-right; an inset hairline rule beneath.
- Hero greeting card (`.boardCard`): muted dateline ("TUESDAY 30 JUNE",
  `Font.label`), then the time-of-day greeting + `userName` on `FlapCell`s,
  stacked ONE WORD PER LINE (GOOD / AFTERNOON / PAUL), big and auto-sized — cell
  size = largest (capped) at which the longest word fits the card. Card HUGS the
  line count (3 lines short name, 4 lines long name) with a CONSTANT gap above
  the docked button — no reserved max height.
- Primary control: the shared `MachinedDisc` forward-arrow docked centred on the
  card's base (half on card / half on page), full depth, no ring. Caption
  "ASSEMBLE YOUR BRIEFING" beneath. Tap = the existing generate→play flow.
- Dropped the old first-run hint + two-tone board scaffolding. Kept the
  injectable `now` + the DEBUG long-press-P first-run reset.

**`Features/Home/HomeContainerView.swift`** — pass `onRefresh` (stubbed
`refreshBriefing` — regenerate-discarding-heard wired later; kept quiet). Play
(arrow) + Profile (P → ProfileFiltersView) wiring unchanged.

**`Tests/HeadlinesUITests`** — the two Home tests tapped the old play-button
coordinate / expected the old profile form. Updated to tap the accessibility
labels ("Assemble your briefing", "Profile") and assert the new screens. Both
pass: arrow → player presents; P → light settings filters opens.

### State
- Builds with 0 errors; the two Home UI tests pass. Verified on sim: short name
  ("PAUL", 3 lines) and long name ("ISABELLA INDIA", 4 lines) — card hugs
  content, constant gap above the docked arrow, no collision/dead space;
  masthead + docked arrow depth + caption read right and match onboarding.

### Next Step
- Wire the refresh action (regenerate discarding already-heard stories).

## Changes Made This Session (2026-06-30 — Build Your Briefing (filters) rebuilt in the LIGHT register + shared two contexts)

### iOS — Onboarding step 2 + settings filters rebuilt to the light world

Rebuilt the **filters / Build Your Briefing** screen into the locked LIGHT
register and made it ONE shared screen used in TWO contexts (onboarding +
settings), so they can never drift apart. The tri-state tree LOGIC
(`FilterTreeModel`) is reused verbatim — this was a re-skin + a shared-component
extraction, not a logic rewrite.

**Decision (with Paul):** the tree's data source is the REAL backend taxonomy
(`GET /data/categories`), NOT the `TopicSeed` placeholder — the created profile
must match what the user actually toggled. Onboarding finish still creates a
backend profile (POST) + sets `didOnboard`, so Home Play keeps working.
Selections also persist as leaf-id JSON for the settings-context rehydration.

**Backend taxonomy shape (confirmed against prod):** 2-level — groups
(Politics, World, Business, Technology, Entertainment, Sport) carry
subcategories; some groups (Science, Climate, Health) are flat (empty
subcategories) and render as single toggle rows. `TopicNode.tree(from:)` maps
both cleanly. No 3rd-level nesting exists in the live data; the partial/half
state still works (parent → partial when only some children are on).

**New — `Shared/Components/BottomActionBar.swift`**
- Extracted the locked footer (inset hairline rule + CTA label left + shared
  `MachinedDisc` arrow right, `arrowDiam = 56`) into one component. Create
  Profile + both filter contexts now use it, so the footer is identical.

**New — `Features/Filters/LightFilterTree.swift`**
- Light skin of the tri-state tree: `LightToggle` (filled dark = on, outline
  grey = off, centred knob + dot = partial), `LightFilterRowView` (ink caps via
  `Font.label`, hairline dividers, staggered indent, parent bolder/larger),
  `LightFilterTreeView`. Drives the SAME `FilterTreeModel` (cascade/partial/
  expand reused unchanged); the dark `FilterTreeView`/`BoardToggle` stay for the
  board world.

**New — `Features/Filters/FiltersScreen.swift`**
- The shared light screen, parameterised by context: `ctaLabel`,
  `showStepIndicator`, `initialSelection` (nil → onboarding defaults), and an
  async `onComplete(leafIDs)`. Owns category load (real backend), the model,
  loading/failed/ready states, optional "2 OF 2", the scrolling tree, and the
  pinned footer (BottomActionBar + soft fade). Onboarding defaults pre-light the
  `world` + `business` groups.
- Also holds `ProfileFiltersView` — the SETTINGS-context wrapper: loads the
  user's current profile, pre-lights its `includeCategories`, CTA "DONE", no
  step indicator; saves edited categories back via `updateProfile` (preserving
  name/duration/topStories), persists the JSON, dismisses. Falls back to the
  saved leaf-id JSON if the profile can't be read.

**`Features/Onboarding/BuildBriefingView.swift`** — rewritten as a thin
onboarding wrapper over `FiltersScreen` (CTA "COMPLETE SETUP", defaults, step
indicator). Finish = create profile (slugs from leaf-ids) → persist id + JSON →
`didOnboard = true` → `onContinue()`. Struct name + signature unchanged, so the
launch flow (createProfile → buildBriefing → home) is untouched.

**`Features/Onboarding/CreateProfileView.swift`** — footer swapped to the shared
`BottomActionBar` (no behaviour change; guarantees identical footer).

**`Features/Home/HomeContainerView.swift`** — the "P" now presents
`ProfileFiltersView` (settings context) via `fullScreenCover`, replacing the old
`ProfileSheet`/`ProfileFormView` sheet. NOTE: the now-unused private
`ProfileSheet` struct + the `ProfileFormView` file are left in place (dead code)
pending confirmation before deletion.

**`Headlines.xcodeproj/project.pbxproj`** — registered the 3 new files.

### State
- Builds with 0 errors. Verified on sim against the prod backend: onboarding
  context (no heading, "2 OF 2", real categories, World+Business pre-lit,
  "COMPLETE SETUP" + arrow); a parent (World) expanded showing nested children
  with one child off → parent renders the partial half-toggle (cascade logic =
  unchanged `FilterTreeModel`); and the settings context via the "P" — same
  tree/toggles/footer, "DONE", no step indicator, pre-loaded with profile 1's
  real selections (Politics on, Business + Sport partial).

### Next Step
- Decide whether to delete the dead `ProfileSheet`/`ProfileFormView` now that the
  light filters screen owns profile editing. Wire real categories' default
  pre-lit set once the taxonomy is finalised (currently world + business).

## Changes Made This Session (2026-06-30 — Create Profile rebuilt in the LIGHT register)

### iOS — Onboarding step 1 rebuilt to the locked light world

Rebuilt the onboarding **Create Profile / name-entry** screen from the old
dark full-screen world into the locked LIGHT register: a contained dark
flap-board card on a near-white page, matching the home's material language.

**New — `ios/Headlines/Headlines/Shared/Theme/MachinedDisc.swift`**
- Extracted the Home Play button's machined-disc material into a shared
  `MachinedDisc` view + `MachinedDiscButtonStyle` (radial charcoal gradient +
  top-edge highlight + matte grain + soft drop shadow + press-in) so the Play
  disc and the onboarding/filters forward arrow render pixel-identically.
- Also holds the shared `Triangle` (play) and new `ForwardArrow` (→) shapes.
- `MachinedDisc(dimmed:)` drops the whole control back for disabled states.

**`ios/Headlines/Headlines/Shared/Theme/Board.swift`**
- Added `LightColors` tokens — `page` `#F7F7F5`, `ink` `#141414` — the light
  register shared by the new home, Create Profile, and (next) the filters screen.

**`ios/Headlines/Headlines/Features/Home/HomeView.swift`**
- Refactored `playButton` to compose the shared `MachinedDisc` /
  `MachinedDiscButtonStyle`; removed its private `Triangle` + `PlayButtonStyle`.
  Output is pixel-identical (verified on sim) — position/size unchanged so the
  coordinate-based Home UI tests still pass.

**`ios/Headlines/Headlines/Features/Onboarding/CreateProfileView.swift`**
- Full rewrite to the light register: near-white page; a contained dark
  flap-board card with "CREATE PROFILE" on shared `FlapCell` tiles (the one
  flap moment) + a real `TextField` whose placeholder ("What is your name?")
  sits ON the input line at the same size the typed name displays. Typed name
  renders off-white plain text (NOT cells) with a visible caret.
- Locked footer pattern: an inset hairline rule (margins both sides), a
  "BUILD YOUR BRIEFING" CTA label (`Font.label`, ink caps) on the left, and the
  machined forward-arrow disc bottom-right. Both lit only when a non-empty name
  is entered, dim/disabled when empty.
- Tap-outside dismisses the keyboard; arrow/`onSubmit` persists `userName` and
  advances. `didOnboard` is still NOT set here (set after the filters step).
- `StepDots` kept (used by the dark filters screen `BuildBriefingView`); Create
  Profile uses its own light "1 OF 2" label instead.

**`ios/Headlines/Headlines.xcodeproj/project.pbxproj`**
- Registered `MachinedDisc.swift` in the Theme group + Sources build phase
  (project lists files manually — not synchronized groups).

### Routing
- Launch flow unchanged: `HeadlinesApp` still routes
  `.createProfile → CreateProfileView { phase = .buildBriefing }`. The struct
  name + `onContinue` signature are preserved, so the new light version drops
  straight into the existing loader → onboarding → home gating.

### State
- Builds with 0 errors. Verified on the iPhone 17 Pro sim: empty state
  (placeholder on the line, arrow + CTA dimmed), typed state ("Paul" off-white
  with caret, placeholder cleared, arrow + CTA lit), and Home Play button
  unchanged after the disc extraction.

### Next Step
- Build the **filters screen** to the same light register using the locked
  footer pattern + the same `MachinedDisc` arrow (same `arrowDiam = 56`).

## Changes Made This Session (2026-05-25 — iOS device fixes, TestFlight prep, cost model restructure)

### iOS — Build fixes

**`ios/Headlines/Headlines/Core/BulletinPlayer.swift`**
- Swift 6 concurrency fix: wrapped `handlePeriodicTime` call in `MainActor.assumeIsolated` (line ~377) — compiler error: `Call to main actor-isolated method in synchronous nonisolated context`
- KVO nil-emission bug fix: added `guard playerState != .buffering else { return }` in `handleCurrentItemChanged` — `AVQueuePlayer` fires `currentItemChanged → nil` during init before items become current; previously caused premature `.ended` state → Play button disabled

**`ios/Headlines/Headlines/Features/Briefing/BriefingView.swift`**
- Updated `onChange(of:perform:)` to two-parameter form `{ _, phase in` — deprecated in iOS 17

### iOS — TestFlight prep

**`ios/Headlines/Headlines/Info.plist`**
- Added `UIBackgroundModes: [audio]` — required for AVAudioSession to keep playing when screen locks or app is backgrounded
- Added `NSExceptionDomains` entry for `<dev-machine-ip>` (LAN IP) — `NSAllowsLocalNetworking` only covers `localhost`/`127.0.0.1`; Release builds need explicit ATS exception for LAN IP

### Backend — Cost model restructure (GNews-only pull, LLM+TTS on-demand only)

**`backend/src/core/platform/config/settings.py`**
- Added `enable_llm_categorise_fallback: bool = True`

**`backend/.env`**
- Added `# ENABLE_LLM_CATEGORISE_FALLBACK=true` toggle comment

**`backend/src/core/pipeline/data/normalise/categorise/category_service.py`**
- Gated Haiku categorisation fallback on `settings.enable_llm_categorise_fallback` — early return to `unclassified` when disabled

**`backend/src/core/pipeline/data/summarise/handlers/rank_completed.py`**
- Extracted `ensure_story_summaries(ranking_run_id, story_ids)` as public function — 3-phase pattern: DB reads → parallel LLM (`ThreadPoolExecutor`, max 6 workers) → persist
- Content-addressed cache: reuses `(story_id, content_hash, model)` rows from any previous run — zero LLM calls on cache hit
- `handle_rank_completed` simplified to: deduplicate IDs → `ensure_story_summaries()` → emit outbox event

**`backend/src/core/pipeline/devtools/pull_stories.py`**
- Removed step 6 (LLM summarisation); now 5 steps — pull → normalise → cluster → categorise → rank
- Summary line: `Paid API calls: GNews only (LLM categorise fallback if needed)`

**`backend/src/core/api/routes/data.py`**
- Added `_SUMMARISE_BUDGET = 12` constant
- Refactored `_get_or_assemble_bulletin()` into two-session pattern:
  - Session 1: ranking run lookup + cache check + candidate extraction (no summaries yet)
  - `ensure_story_summaries()` call (no DB held open during LLM calls)
  - Session 2: read summaries, select by duration, assemble, persist bulletin

### Verification results
- `pull_stories`: GNews only, 0 LLM calls, 0 TTS ✅
- Cold Generate: 7 parallel LLM calls, 10 TTS background tasks, 7.56s manifest response
- Warm Generate (full cache): 0 LLM, 0 TTS, ~100ms
- New bulletin with shared stories: 0 LLM, 3 TTS (new segment text only), ~105ms

### Current State
- iOS: archive-ready except for app icon (user to provide 1024×1024 PNG, no alpha) and `API_BASE_URL` User-Defined build setting in Xcode
- Backend: pull_stories is free (GNews only); all LLM+TTS deferred to Generate time
- Next: app icon + Xcode build setting, then archive → TestFlight

---

## Changes Made This Session (2026-04-30 — Step 3 server completion + iOS streaming player)

### Server-side (Step 3 pre-iOS cleanup)

**Modified `platform/config/settings.py`:**
- Added `public_api_base_url: str = "http://localhost:8000"` — used by manifest endpoint to construct absolute URLs

**Modified `api/routes/profiles.py`:**
- Manifest endpoint now uses `settings.public_api_base_url.rstrip("/")` to construct absolute URLs for all segment URLs
- Story segments now include `"title"` field populated from `stories_list[].headline` — required for iOS lock screen / now playing info

### iOS — Step 3 streaming playback (chunks a–h complete)

**New `ios/.../Models/BulletinManifest.swift`:**
- `ManifestSegment`: index, type, url, durationMs, storyHash, storyId, title; computed `isStory`, `durationSeconds`
- `BulletinManifest`: bulletinId, rankingRunId, segments

**New `ios/.../Core/BulletinPlayer.swift`:**
- `@MainActor ObservableObject` backed by `AVQueuePlayer`
- **a. Audio session:** `setCategory(.playback)` + interruption handler (pause on began, resume if shouldResume) + route change handler (pause on headphone unplug)
- **b+c. Sliding window:** `enqueueNext()` called twice initially; item N's `readyToPlay` triggers N+2 pre-fetch via Combine publisher; `automaticallyWaitsToMinimizeStalling = false`
- **d. Event firing:** `addPeriodicTimeObserver` at 0.1s; `currentPositionPct` = positionSeconds / durationSeconds; "completed" fires on natural item advancement, "skipped" on skip(), "abandoned" on sendSummary()
- **e. Skip:** fires "skipped" event, removes following transition if present, calls `advanceToNextItem()`
- **f. Failure paths:** 4s stall timeout (2×2s); item failure (503) → silent skip via `handleItemFailed()`; manifest fetch failure → `state = .failed(msg)`
- **g. Lock screen:** `MPNowPlayingInfoCenter` updated on segment change (title, artist "Headlines", duration, chapter N of M); `MPRemoteCommandCenter` play/pause/nextTrack
- **h. Summary + lifecycle:** `sendSummary()` batches accumulated events + current abandoned event via `POST /data/bulletins/{id}/summary`; `accumulatedEvents` accumulates per-session events for the safety net
- State machine: idle → loadingManifest → buffering → playing/paused/stalled → ended/failed

**Replaced `ios/.../Features/Briefing/BriefingViewModel.swift`:**
- Removed `AudioPlayer` + `BulletinService` dependencies
- Added `BulletinPlayer` owned by the VM; Combine sinks forward player state to `BriefingViewModel.State`
- `generateBulletin()` → `bulletinPlayer.load(profileId:)`
- `bulletin: BulletinResult?` synthesised from manifest (stories with title+storyHash, no startTime)
- `handleBackground()` / `handleForeground()` for lifecycle (5-min re-fetch on foreground)

**Modified `ios/.../Features/Briefing/BriefingView.swift`:**
- Added `@Environment(\.scenePhase)` + `.onChange(of:)` → `vm.handleBackground/Foreground()`
- State label: `.generating` → "Fetching bulletin…", `.downloadingAudio` → "Preparing audio…"
- `.buffering` maps to `.readyToPlay` — PlayerView shows immediately after manifest loads

**Updated Xcode project (`project.pbxproj`):**
- Added `BulletinPlayer.swift` to Core group
- Added `BulletinManifest.swift` to Models group
- Both in Sources build phase

**Build result:** `** BUILD SUCCEEDED **` (no errors, iOS Simulator target)

### Current State
- Server: manifest endpoint returns absolute URLs + title fields; user_story_state tracking complete
- iOS: full streaming playback implemented; events fire; lock screen works; lifecycle handled
- Next: device testing, PlaybackView migration (old AVAudioPlayer-based flow still exists in PlaybackView)

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
`NSAllowsLocalNetworking` only covers `127.0.0.1`/`.local`. Physical device hitting `http://<dev-machine-ip>:8000` is blocked by default ATS. The project uses `GENERATE_INFOPLIST_FILE = YES` (no Info.plist exists), so ATS can't be configured without creating one.

**Problem 3 — URL hardcoded to 127.0.0.1:**
Simulator-only default. Device needs Mac LAN IP.

**Files changed:**
- `Core/APIClient.swift` — rewrote to fix struct closure bug; extracted `buildURL()` + `send()` helpers; all methods now inside struct
- `Core/AppConfig.swift` — `#if targetEnvironment(simulator)` auto-selects `127.0.0.1` for simulator, `deviceLANIP` constant for device; one-time edit: set `deviceLANIP = "<dev-machine-ip>"` (current Mac LAN IP)
- `Headlines/Info.plist` — **new file** replacing auto-generated plist:
  - Replicates all `INFOPLIST_KEY_*` build settings (UIApplicationSceneManifest, UILaunchScreen, UISupportedInterfaceOrientations, etc.)
  - Adds `NSAppTransportSecurity`: `NSAllowsLocalNetworking = true`, `NSAllowsArbitraryLoadsInDebug = true` (debug builds only — no ATS impact on release)
  - Adds `API_BASE_URL = $(API_BASE_URL)` build variable hook for per-scheme URL override

**Required Xcode steps (one-time, manual):**
1. In Xcode project navigator: right-click `Headlines` group → Add Files → select `Info.plist`
2. Build Settings → search "Generate Info.plist" → set to **No** for Debug and Release
3. Build Settings → search "Info.plist File" → set to `Headlines/Info.plist`
4. In `AppConfig.swift` line `static let deviceLANIP = "YOUR_MAC_LAN_IP"` → replace with `"<dev-machine-ip>"` (or your current LAN IP — run `ipconfig getifaddr en0` to check)

**Audio URL handling (all cases covered):**
| Scenario | `audioUrl` in response | How iOS downloads |
|---|---|---|
| Local dev (simulator) | `null` | `service.audioFileURL()` → `http://127.0.0.1:8000/dev/api/audio/{id}/file` |
| Local dev (device) | `null` | `service.audioFileURL()` → `http://<dev-machine-ip>:8000/dev/api/audio/{id}/file` |
| S3 production | `https://bucket.s3.region.amazonaws.com/...` | URL used directly, no Mac involved |

**Validation:**
- `GET http://<dev-machine-ip>:8000/data/profiles` → `200 {"profiles":[...]}` ✓
- `GET http://<dev-machine-ip>:8000/dev/api/audio/13/file` → `200 1,587,453 bytes` ✓
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
- `deviceLANIP = "YOUR_MAC_LAN_IP"` → `"<dev-machine-ip>"`
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

## Changes Made This Session (2026-04-29 — audio experience wiring + validation)

### Gaps closed

**`src/core/pipeline/data/summarise/handlers/rank_completed.py`** — category pass-through:
- Added `story_categories: dict[str, str | None] = {}` to Phase 1 locals
- Batch-queries `data.stories.primary_category` for all pending story IDs in Phase 1 DB session (one `ANY(:ids)` query, no N+1)
- Phase 2 now passes `category=story_categories.get(story_id)` to `summarise_story()` so LLM tone guidance actually fires

**`src/core/api/routes/data.py`** — `is_first_bulletin` detection:
- Added JSONB COUNT query before `assemble()` call: `SELECT COUNT(*) FROM data.bulletins WHERE filters @> jsonb_build_object('name', :name)`
- Zero prior rows → `is_first_bulletin=True` → first-time intro template fires
- Only runs when `name` is set and no cached bulletin already exists
- `assemble()` call now passes `is_first_bulletin=is_first_bulletin`

**Bulletin cache cleared** — deleted 8 `bulletin_audio` rows then 8 `data.bulletins` rows for profile "Paul" (FK-ordered, no data loss; these were all old cached bulletins assembled before the new assembler code).

### Validation (script-only — ElevenLabs credits exhausted)

Direct Python assembly test (`ranking_run_id=16`, 5 stories, `name="Paul"`, `is_first_bulletin=True`):

```
INTRO:  "Good to have you, Paul. Let's start you off."   ← first-time template
STORY 1: climate  — High pressure / mostly dry week
TRANS:  [silent pause]                                    ← ~15% pause roll
STORY 2: politics.uk — King Charles US visit security
TRANS:  "In public health..."                             ← category-matched
STORY 3: health   — Wife spots dementia signs
TRANS:  "Over in Westminster..."                          ← category-matched
STORY 4: politics.us — White House press dinner arrest
TRANS:  "Something a little more cheerful..."             ← tonal bridge (heavy→lighter)
STORY 5: world.us — Yellow fever surge warning
OUTRO:  "That's everything for now. Back later if anything breaks."  ← 2-part morning
```

299 words · 5 stories · 11 segments — within 3-minute target.

**Note:** Existing summaries in DB were generated before the category-aware prompt was wired. Cached summaries (same story content) will be reused as-is; new tone-guided summaries will only appear when story content changes on the next ingest cycle.

**ElevenLabs quota:** ~45 credits remaining. Need top-up or switch to `TTS_PROVIDER=openai` before next audio generation.

---

## Changes Made This Session (2026-04-28 — audio experience pass)

### Files changed

**`src/core/pipeline/data/summarise/llm_summariser.py`** — prompt + config only:
- `_MAX_TOKENS` 600 → 950 (supports longer summaries)
- `summarise_story()` gains optional `category: str | None = None` parameter (backward compatible)
- `_TONE_GUIDANCE` dict: category → tone instruction (8 categories)
- `_build_prompt()` gains `category` parameter; passes tone guidance to LLM
- `audio_script` target: 70-85 words → 4-6 sentences, 120-180 words
- First sentence of `audio_script` must be a hook: specific fact + tension + no banned openers
- Extended forbidden phrases: added "officials are considering", "the situation remains", "many are wondering", "in a developing story", "more on this as it unfolds", "the story continues to develop", "it remains unclear", "amid uncertainty"
- Forward-looking close requirement added
- Specific-fact requirement (one per two sentences) added
- Snippet length 300 → 400 chars per article

**`src/core/pipeline/data/bulletin/pronunciation.py`** (new):
- `normalise_numbers()` — converts £2.3bn → "2.3 billion pounds", 10% → "10 percent"
- `normalise_acronyms()` — spaces HMRC, DVLA, ASOS (observed ElevenLabs problem cases)
- `apply_pronunciation_overrides()` — whole-word substitutions for 18 proper nouns
- `normalise_for_tts()` — master pass applied to assembled script before TTS
- `PRONUNCIATION_OVERRIDES` dict as extension point; `_ACRONYM_AS_WORD` documents leave-as-is set

**`src/core/pipeline/data/bulletin/transitions.py`** (new):
- 97 total transition phrases (≥ 80 target): 10 per category × 9 categories (90) + 8 tonal heavy→light + 6 tonal light→heavy + 9 geographic + 7 thematic + 14 neutral
- `_is_heavy()` heuristic: category ∈ {world, health, climate, politics} + heavy keyword in story text
- `pick_transition()`: takes previous/next story, position, total count, used set; ~15% pause; biases toward tonal bridges after heavy stories; full bulletin dedup via `used_in_bulletin` set

**`src/core/pipeline/data/bulletin/intros.py`** (new):
- 15 named template functions (all distinct rhythm/structure)
- `_FIRST_TIME_TEMPLATES` — 3 friendlier first-bulletin variants
- `_BRIDGES` pool — 8 hook→greeting bridges
- `build_intro()`: time-of-day filtering, story-count awareness, name enforcement (name always appears when provided), is_first_bulletin support
- `_extract_hook()` — pulls first sentence of `audio_script` (20-220 char sentence boundary)
- `_est_minutes()` — rough spoken duration estimate from story count

**`src/core/pipeline/data/bulletin/outros.py`** (new):
- 8 caught-up variants with name + story count (e.g. "Five stories and you're sorted, Paul.")
- 8 caught-up variants without name
- 6 morning / 6 afternoon / 6 evening forward phrases
- `_SIGNOFFS` pool with blank-weighted distribution
- `build_outro()`: 3 structure variants (1-part 20%, 2-part 45%, 3-part 35%); name used ~55% of time

**`src/core/pipeline/data/bulletin/assembler.py`** (rewritten):
- Imports and delegates to all four new modules
- Passes `used_in_bulletin` set to `pick_transition()` for full within-bulletin dedup
- Passes previous + next story dicts to transition selector
- Applies `normalise_for_tts()` to final assembled script
- `is_first_bulletin` parameter plumbed through to `build_intro()`
- Filters empty-string transitions (pause) from script join but preserves paragraph break

## What Is Incomplete

- **Summaries require API key** — set `ANTHROPIC_API_KEY` in `.env` to activate
- **S3 boto3 not installed** — run `poetry install -E s3` before switching to S3 mode
- **No automated tests** — pipeline health check devtool covers ingest→cluster; no unit/integration tests

## Next Steps

- Install boto3 and test S3 upload against a real bucket (R2 recommended — free tier)
- Wire summaries/bulletins into feeds/scripts/audio pipeline stages (TTS stage)
- `GET /data/bulletins/latest` returns most recently *created* bulletin — may want `GET /data/bulletins/assemble?cached_only=true` variant

## 2026-07-11 — Cold-path zero-playback regression (Meta bulletin, profile 28, 16:48 UTC)

**Symptom:** from-scratch briefings loaded with the lead greyed, 0:00, gate never opened; cached
briefings played fine. First on-device test of the L-C/L-D series.

**Convicted — NOT the L-series.** Traced prod bulletin 255 (lead 489191 "Meta pulls new AI image
feature"). Server side was fully healthy: run divergence False (bulletin run 9712 == edition pinned
9712), `gate_opener_ok` True, no synth failures, full assembly finalised, both gate segments have
audio. So no assertion crash / no L-C block / no L-D rejection.

**Root cause = a timeout dead-zone.** Timeline: bulletin created 16:48:16; intro audio ready
16:49:27 (+71s); opener audio ready 16:49:29 (+73s). The iOS readiness gate capped at **60s**
(`BulletinPlayer.swift:386`) while the server stall watchdog is **90s** (`SEGMENT_STALL_SECONDS`).
Ordering `60s < 71s < 90s`: the client quit after giving up but before the audio was ready and
before the watchdog would flag a stall, then fell through and called `player.play()` on URL-less
gate segments with no retry → permanent grey. Cold-only because cached gate audio makes
safe_to_start fire on poll #1.

**Fix (files changed):** `ios/.../Core/BulletinPlayer.swift` — raised the readiness ceiling
60s→100s (above the 90s watchdog) and added a `readinessSeen && !reachedSafe` guard that surfaces
a "taking longer than usual, pull to refresh" retry instead of dead-playing. Never starts playback
without server `safe_to_start`. Build: `xcodebuild ... BUILD SUCCEEDED`. PR #203.

**State:** client fix applied + compiles. Not yet device-retested against a live cold briefing.

**Next step:** on-device retest of a from-scratch briefing (cold cache) to confirm the ~71s cold
synth now plays through; consider a follow-up to trim the ~71s cold first-audio critical path
(greeting Haiku + first-two summarise + intro/opener TTS are all serialised before the gate).
