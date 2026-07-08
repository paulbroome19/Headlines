# Audit follow-ups (headlines-audit-2026-07-07.md)

Tracking of fixes shipped for the trust-audit risks, and gotchas for future debugging.

## Risk #1 — stale `top_story` on the ranked list — FIXED (PR #155)
`maintain_ranked_list` re-stamps `top_story` from the latest editorial verdict every reviewed run.
Step 2 (order on `significance_delta`) was dropped — the signal is too noisy (60% of stories
wobble, ~200 flips/day). See memory `ranked-list-editorial-restamp`.

**Re-measured post-#156 (2026-07-08, 48h window):** the heuristic clustering fix did **NOT**
meaningfully reduce the wobble — per-story wobble is still **54.8%** (270 multi-run stories, 148
wobbled; baseline 60%), and **38/45 (84%)** of the current top-50-by-merit still flip (baseline
47/49 = 96%). Flip volume was higher (1,620 flips/48h ≈ 810/day vs ~200) but that tracks run
frequency, not stability. **Verdict: the wobble is not primarily clustering-driven — semantic
clustering / significance-scoring stability (issue #36) is still warranted.** `significance_delta`
remains unsafe to order on.

## Risk #2 — entity-slug clustering over-merge — FIXED (this PR: cluster over-merge)
Bare entity-slug clustering collapsed unrelated events into multi-day mega-clusters (nato = 51
articles / 5 days; white-house = 6 unrelated stories) because the anchor match keyed on the entity
slug ALONE within a *sliding* 24h window that never closed for a continuously-covered entity.

Fix, in `cluster/handlers/requested.py`:
1. **Hard age cap from birth** — anchor match requires `first_published_at > now() - 48h` (plus a
   24h recency check). A cluster rolls over every ≤48h instead of sliding for days; aligns with the
   ~36h ranking candidate window.
2. **Top-level category gate** — anchor match adds `split_part(primary_category,'.',1) = :topcat`,
   so one entity can't merge across topics (white-house culture vs sport vs world).
3. **Anchor-eligibility (`anchor_eligible`) in `entities.yml`** — broad institutions/venues that
   generate continuous unrelated coverage are demoted from anchoring and fall to the richer hint
   path (first-title-word + category). Currently flagged: `white-house, congress, senate,
   european-union, nato, united-nations`. Add the flag to `downing-street, pentagon, kremlin` etc.
   if they are ever added to the registry.

**⚑ DEBUGGING NOTE — if a future story traces to an over-merged bare-slug cluster, CHECK THE
`anchor_eligible` FLAG FIRST.** A broad institution that slipped in without the flag is the most
likely cause: set `anchor_eligible: false` on it in `entities.yml`. See the ANCHOR ELIGIBILITY note
at the top of that file for the demotion criterion.

Residual (out of scope): same-entity **same-category** distinct events within 48h (e.g. two
unrelated NATO stories both in `politics.world`) still merge — key heuristics can't separate them
without semantic clustering (issue #36). The age cap bounds the blob; category + demotion split the
cross-topic cases.

Migration: **age-out**, no retroactive split. Existing mega-clusters stop growing under the new
match and age out of the 36h candidate window + the 24h `ranked_stories` prune within ~1–2 days.

**✅ VERIFIED post-deploy (2026-07-08, ~24h after 5c8c380 deployed 2026-07-07 21:02 UTC; read-only prod):**
- **Mega-blobs frozen at deploy, no new demoted over-merges.** nato `5074` (last article 07-07 18:56),
  white-house `8326` (07-07 13:35) and senate `58530` (07-07 18:38) received **zero** articles after
  the deploy — all frozen pre-deploy, aging out. Of 13 clusters still spanning >48h in the last 36h,
  the **4 that grew post-deploy are all legit `hint:`/live-blog clusters** (`ent=None`, e.g. the
  World-Cup live blog `657`, 7d/88 arts) — none are demoted bare-slug over-merges.
- **New white-house articles now land on coherent per-category `hint:` clusters** — `329218`
  (politics.us "Freedom Fuel station"), `281187` (business.economy "Freedom Fuel gas stations"),
  `272390` (politics.us) — titles match category; no 6-unrelated-events blob.
- **One residual edge:** a single-article bare-slug `congress|…` cluster (`243380`) appeared
  post-deploy — a 1-article leak, not a blob (watch the `anchor_eligible` flag for `congress`).
- Baselines vs now: MAX span among last-36h-active clusters is now a **live blog** (7d, story 657),
  not a demoted blob; nato `5074` and white-house `8326` are static. **Risk #2 fix confirmed working.**

## Risk #3 — headline↔standfirst mismatch — GUARDED (this PR)
Home preview headline = live representative article; standfirst = latest cached summary. If the
summary's `content_hash` no longer matches the story's current top-5 articles, the standfirst is
**suppressed** (`_coherent_standfirst`, `routes/data.py`) rather than showing text about a different
event. Cheap read-layer guard; independent of the clustering fix.

## Test gaps (recurring holes — add coverage here)
The play-event → consumption chain has regressed **three times** on links that had no test:
1. **iOS payload ↔ endpoint schema (the /summary 422).** The batch `/summary` reused
   `BulletinEventRequest` (per-event `profile_id` REQUIRED) while iOS sends `profile_id` only at the
   top level → every flush 422'd and recorded nothing, invisibly. **A contract test between the iOS
   payload and the endpoint schema would have caught this on day one.** Now covered:
   `routes/test_summary_contract.py` validates the exact iOS flush JSON (build 45 + 46) against
   `BulletinSummaryRequest`, and guards that per-event `profile_id` is NOT required. **When the iOS
   flush payload changes, update that fixture in lockstep.**
2. **Consumption keyed on story_hash, not story_id** (hash drifts across bulletins / split lead) —
   now covered by `repos/test_consume_by_story_id.py`.
3. **iOS flush exit-path** (capture-before-teardown; view-.task cancelled on dismiss) — now covered
   by `HeadlinesTests.testDetachSummaryCapturesEventsAndSendsStoryId`.

Still open: a true **live-DB end-to-end** test of `/summary` (POST → transition → read-path filter)
— the suite has no DB fixture; the chain is verified by unit tests + the on-device loop + the
`summary:` server log (now WARNING-level and returned in the response so it survives the ingest
log flood). Diagnostics that get rate-limited out aren't diagnostics — keep flush signals on a
surviving path (response body / client os_log), not just server stdout.
