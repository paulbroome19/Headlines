# Audit follow-ups (headlines-audit-2026-07-07.md)

Tracking of fixes shipped for the trust-audit risks, and gotchas for future debugging.

## Risk #1 — stale `top_story` on the ranked list — FIXED (PR #155)
`maintain_ranked_list` re-stamps `top_story` from the latest editorial verdict every reviewed run.
Step 2 (order on `significance_delta`) was dropped — the signal is too noisy (60% of stories
wobble, ~200 flips/day). See memory `ranked-list-editorial-restamp`.

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

## Risk #3 — headline↔standfirst mismatch — GUARDED (this PR)
Home preview headline = live representative article; standfirst = latest cached summary. If the
summary's `content_hash` no longer matches the story's current top-5 articles, the standfirst is
**suppressed** (`_coherent_standfirst`, `routes/data.py`) rather than showing text about a different
event. Cheap read-layer guard; independent of the clustering fix.
