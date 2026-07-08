"""
ONE selection, two renderings (audit §3).

Home preview and the briefing must not be two parallel selection paths we keep patching
into agreement — they must render the SAME materialised selection. `build_selection` is the
single canonical pipeline; `resolve_materialised_selection` runs it once per (profile, day,
request_hash), pins it to a ranking run, and stores the result so both the preview and the
final briefing read identical ordered story ids.

Selection reads the stable ranked list (data.ranked_stories) — the only selection path
(the read_from_ranked_list flag and the live-scoring fallback were retired: audit §7 B.9).

Order (per the task): qualify_and_order → dropped-filter → dedup_same_event → cap_bulletin.
dedup runs BEFORE cap (so cap fills N with distinct events) and operates on the candidates'
representative titles, so it needs no summaries. `dedup_same_event` keeps the best-ranked
(lowest-index) member of each same-event group, so the LEAD (index 0) can never be dropped —
the opener gate always synthesises the correct first story.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.pipeline.data.bulletin.connective import uk_now
from core.pipeline.data.bulletin.edition import UserDailyEditionRepo, _categories_for
from core.pipeline.data.bulletin.event_dedup import dedup_same_event
from core.pipeline.data.bulletin.repos.bulletin_repo import BulletinRepo
from core.pipeline.data.bulletin.repos.user_story_state_repo import UserStoryStateRepo
from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.depth import depth_for_rank
from core.pipeline.ranking.ranked_list import scored_from_ranked_list
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo
from core.pipeline.ranking.thresholds import (
    DEFAULT_PRESET,
    PRESET_BAR,
    _fresh_category_relief,
    _is_top_candidate,
    _matches_category,
    _selection_context,
    cap_bulletin,
    qualify_and_order,
)

logger = logging.getLogger(__name__)


def _excluder(exclude_categories: list[str] | None):
    excludes = list(exclude_categories or [])

    def _excluded(cat: str | None) -> bool:
        return bool(cat) and any(cat == e or cat.startswith(e + ".") for e in excludes)

    return _excluded


def _sid_key(story_id: str):
    """Sort key for 'lower story_id' — numeric ids compared numerically, others lexically."""
    return (0, int(story_id)) if str(story_id).isdigit() else (1, str(story_id))


def guard_top_block(
    reservoir: list,
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    preset: str,
    now: datetime | None = None,
) -> list:
    """Deterministic backstop for the LLM fragment-dedup (#156 rider 2b): if two stories in the
    TOP-STORIES block share (primary entity, top-level category), keep the better one and demote
    the rest OUT of the top block. A demoted story stays only if it independently clears the topic
    bar as a normal followed-topic story; otherwise it's dropped (a below-bar / unfollowed-category
    story can't legitimately be a topic story, and must not fall back into the top block).

    Runs AFTER dedup_same_event (backstop for what the LLM misses, not a replacement) and BEFORE
    cap_bulletin, so preview/blocking/streaming inherit it identically. Top-block-ONLY: followed-
    topic buckets are untouched (two same-entity stories in a topic feed are legitimate). No-entity
    (hint-path) stories are exempt — never grouped. Deterministic; no LLM. The lead (index 0) is
    always its group's kept member, so the guard can never change the lead."""
    if len(reservoir) < 2:
        return reservoir

    toggled_regions, topic_cats = _selection_context(include_top_stories, include_categories)
    ref_now = now or datetime.now(timezone.utc)
    bar = PRESET_BAR.get(preset) or PRESET_BAR[DEFAULT_PRESET]
    lead_id = reservoir[0].candidate.story_id

    def _in_top(s) -> bool:
        return _is_top_candidate(s.candidate.primary_category, s.candidate.top_story, toggled_regions)

    def _group_key(s):
        ent = s.candidate.primary_entity_id
        if not ent:
            return None  # no primary entity → exempt, never grouped
        return (ent, (s.candidate.primary_category or "").split(".")[0])

    def _clears_topic_bar(s) -> bool:
        cat = s.candidate.primary_category
        return (s.normalized_score >= (bar - _fresh_category_relief(s, ref_now))
                and any(_matches_category(cat, tc) for tc in topic_cats))

    groups: dict = {}
    for s in reservoir:
        if not _in_top(s):
            continue
        k = _group_key(s)
        if k is None:
            continue
        groups.setdefault(k, []).append(s)

    remove: set[str] = set()
    for k, members in groups.items():
        if len(members) < 2:
            continue
        # Winner: the lead if it's in this group (invariant — index 0 is never demoted); else the
        # higher-merit story (tiebreak lower story_id).
        winner = next((m for m in members if m.candidate.story_id == lead_id), None)
        if winner is None:
            winner = min(members, key=lambda s: (-s.normalized_score, _sid_key(s.candidate.story_id)))
        for loser in members:
            if loser is winner:
                continue
            loser.candidate.top_story = False   # demote out of the top block
            kept = _clears_topic_bar(loser)
            if not kept:
                remove.add(loser.candidate.story_id)
            logger.info(
                "top_block_guard: demoted story=%s (shared entity+topcat=%s with kept=%s) "
                "kept_as_followed_topic=%s", loser.candidate.story_id, list(k),
                winner.candidate.story_id, kept,
            )

    if not remove:
        return reservoir
    return [s for s in reservoir if s.candidate.story_id not in remove]


# ── Deterministic whole-bulletin same-event backstop ────────────────────────────────────────
# The LLM dedup (event_dedup) is the primary same-event pass; when it fails (and now fails LOUD,
# not silent) a paraphrased duplicate can still reach the bulletin — the most visible failure to
# a new user is two near-identical stories back-to-back. This is the deterministic net: within a
# section (top-level category) it collapses two stories that are the SAME EVENT, keeping the
# higher-merit one. No LLM, no network, no latency.
#
# Same-event test = real title overlap AND at least TWO shared DISTINCTIVE (rare-in-section)
# tokens. The two-token requirement is the crux: a genuine paraphrased duplicate shares MULTIPLE
# rare subject tokens ('farage' AND 'clacton'), whereas two genuinely-different stories that only
# look alike share exactly ONE (two World Cup fixtures share 'england' but their opponents —
# 'france' vs 'spain' — differ; two matches for one team share the team but not the outcome). So
# one shared rare token is never enough to merge, which is what makes this safe against deleting
# real news. A shared primary entity counts as one of the two, so an entity-resolved paraphrase
# needs only one more shared distinctive token.
_DUP_TITLE_MIN_OVERLAP = 0.50       # gate: titles must genuinely overlap, not just share rare tokens
_DUP_MIN_SHARED_DISTINCTIVE = 2     # ≥2 shared rare signals (entity counts as one) to call same-event
_DISTINCTIVE_DF_MAX = 2             # absolute floor: a token in ≤2 stories is rare (small sections)
_DISTINCTIVE_DF_FRACTION = 0.15     # …or in ≤15% of a larger section (a generic word like 'england'
                                    # is common enough to sit above this, so two different fixtures
                                    # sharing only 'england' never reach the ≥2 rare-token bar)
_DUP_STOP = frozenset({
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "as", "is", "his", "he", "she", "by",
    "with", "at", "from", "it", "its", "be", "are", "was", "has", "have", "will", "who", "that",
    "this", "after", "over", "into", "out", "up", "not", "but", "or", "they", "their", "says",
    "said", "new", "how", "why", "what", "amid", "set", "may",
})


def _title_tokens(title: str) -> set[str]:
    """Normalised content tokens: lowercase, drop stopwords, light-stem (plural/-ing/-ed), keep
    length ≥ 2 (so 'mp', 'uk', 'us' survive). Set semantics — order/repeats don't matter."""
    out: set[str] = set()
    for w in re.findall(r"[a-z0-9]+", (title or "").lower()):
        if w in _DUP_STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                break
        if len(w) >= 2:
            out.add(w)
    return out


def _title_overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient |A∩B| / min(|A|,|B|) — robust to one headline being longer."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def dedup_bulletin_events(reservoir: list, *, titles: dict[str, str] | None = None) -> list:
    """Deterministic same-event collapse across the WHOLE reservoir (see block comment above).
    Compares the USER-FACING title (representative_title, via `titles`; falls back to candidate
    title) — that's what the listener perceives as a duplicate, and it can differ from the raw
    cluster-anchor title the ranking carries. Keeps the higher-merit member of each same-event
    group (tiebreak lower story_id); the lead (index 0) is always its group's kept member, so the
    guard can never change the lead. No LLM."""
    if len(reservoir) < 2:
        return reservoir

    tmap = titles or {}
    def _disp(s) -> str:
        return tmap.get(s.candidate.story_id) or s.candidate.title or ""

    toks = {s.candidate.story_id: _title_tokens(_disp(s)) for s in reservoir}
    sect = {s.candidate.story_id: (s.candidate.primary_category or "").split(".")[0] for s in reservoir}
    # per-section document frequency of each token, and section sizes → distinctiveness
    df: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    section_size: dict[str, int] = defaultdict(int)
    for s in reservoir:
        section_size[sect[s.candidate.story_id]] += 1
        for t in toks[s.candidate.story_id]:
            df[sect[s.candidate.story_id]][t] += 1

    def _distinctive(t: str, section: str) -> bool:
        # rare within the section — ≤2 stories (small-section floor) OR ≤15% of a larger section —
        # AND not present in EVERY story of a tiny section, so in a 2-story section nothing is
        # "distinctive" and two different formulaic fixtures are never merged on the title path.
        d = df[section][t]
        cap = max(_DISTINCTIVE_DF_MAX, _DISTINCTIVE_DF_FRACTION * section_size[section])
        return d <= cap and d < section_size[section]

    def _same_event(a, b) -> bool:
        sid_a, sid_b = a.candidate.story_id, b.candidate.story_id
        if sect[sid_a] != sect[sid_b]:
            return False  # only ever collapse WITHIN a section
        ta, tb = toks[sid_a], toks[sid_b]
        if _title_overlap(ta, tb) < _DUP_TITLE_MIN_OVERLAP:
            return False
        shared_signals = sum(1 for t in (ta & tb) if _distinctive(t, sect[sid_a]))
        ea, eb = a.candidate.primary_entity_id, b.candidate.primary_entity_id
        if ea and eb and ea == eb:
            shared_signals += 1  # a shared resolved entity is a strong subject signal
        return shared_signals >= _DUP_MIN_SHARED_DISTINCTIVE

    # Representative-anchored grouping — NOT transitive union-find. Each story is collapsed only
    # when it is a same-event match of a group's REPRESENTATIVE (the higher-merit seed), never
    # merged through a chain of intermediates. This is what stops a hot topic (several Iran/oil
    # stories) from transitively sweeping in a tangential one (a UK house-prices story that merely
    # shares 'Iran'): a candidate must resemble the kept story directly, not a bridge. The lead
    # (reservoir[0]) is seeded FIRST, so it is always a representative and can never be dropped.
    by_merit = sorted(reservoir, key=lambda s: (-s.normalized_score, _sid_key(s.candidate.story_id)))
    lead_id = reservoir[0].candidate.story_id
    order = [reservoir[0]] + [s for s in by_merit if s.candidate.story_id != lead_id]

    assigned: set[str] = set()
    remove: set[str] = set()
    for rep in order:
        rid = rep.candidate.story_id
        if rid in assigned:
            continue
        assigned.add(rid)
        for other in order:
            oid = other.candidate.story_id
            if oid in assigned:
                continue
            if _same_event(rep, other):
                assigned.add(oid)
                remove.add(oid)
                logger.info(
                    "bulletin_dedup: dropped story=%s as same-event duplicate of kept=%s (section=%s)",
                    oid, rid, sect[rid],
                )

    if not remove:
        return reservoir
    return [s for s in reservoir if s.candidate.story_id not in remove]


def finalize_selection(
    scored: list,
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    exclude_categories: list[str] | None,
    preset: str,
    dropped: set[str],
    dedup_fn=dedup_same_event,
    now: datetime | None = None,
    titles: dict[str, str] | None = None,
) -> list[dict]:
    """PURE canonical pipeline over an already-scored reservoir: qualify_and_order → drop
    excluded/heard/skipped → dedup_same_event (BEFORE cap, on representative titles) →
    cap_bulletin. Returns ordered [{story_id, primary_category}]. `dedup_fn` is injectable for
    tests. dedup keeps the best-ranked member of each same-event group, so the LEAD survives."""
    reservoir = qualify_and_order(
        scored, include_top_stories=include_top_stories,
        include_categories=include_categories, preset=preset,
    )
    _excluded = _excluder(exclude_categories)
    reservoir = [
        s for s in reservoir
        if not _excluded(s.candidate.primary_category) and s.candidate.story_id not in dropped
    ]
    tmap = titles or {}
    if dedup_fn is not None:
        stub = [{"story_id": s.candidate.story_id,
                 "headline": tmap.get(s.candidate.story_id) or s.candidate.title or ""}
                for s in reservoir]
        kept = {d["story_id"] for d in dedup_fn(stub)}
        reservoir = [s for s in reservoir if s.candidate.story_id in kept]

    # Deterministic top-block dedup: after the LLM same-event dedup (its backstop), before cap.
    reservoir = guard_top_block(
        reservoir, include_top_stories=include_top_stories,
        include_categories=include_categories, preset=preset, now=now,
    )

    # Deterministic WHOLE-bulletin same-event backstop: catches paraphrased duplicates the LLM
    # dedup missed or failed-open on, anywhere in the line-up (not just the top block).
    reservoir = dedup_bulletin_events(reservoir, titles=titles)

    display = cap_bulletin(
        reservoir, include_top_stories=include_top_stories,
        include_categories=include_categories, preset=preset,
    )
    return [{"story_id": s.candidate.story_id, "primary_category": s.candidate.primary_category} for s in display]


def build_selection(
    db: Session,
    *,
    profile_id: int | None,
    include_top_stories: bool,
    include_categories: list[str] | None,
    exclude_categories: list[str] | None,
    preset: str,
    dedup: bool = True,
) -> list[dict]:
    """The canonical ordered selection (from the stable ranked list): load the stable-list
    reservoir, then finalize_selection. `dedup=False` gives the fast pre-dedup order for the
    cold-start streaming skeleton (never on the first-play LLM path)."""
    candidates = load_story_ranking_candidates(db)
    scored = scored_from_ranked_list(db, candidates)
    dropped = (
        {str(x) for x in UserStoryStateRepo(db).get_dropped_story_ids(profile_id)}
        if profile_id is not None else set()
    )
    titles = _representative_titles(db, [s.candidate.story_id for s in scored])
    return finalize_selection(
        scored, include_top_stories=include_top_stories, include_categories=include_categories,
        exclude_categories=exclude_categories, preset=preset, dropped=dropped,
        dedup_fn=dedup_same_event if dedup else None, titles=titles,
    )


def _representative_titles(db: Session, story_ids: list[str]) -> dict[str, str]:
    """{story_id: representative_title} for the candidate set — the USER-FACING title the dedup
    compares. candidate.title is the raw cluster-anchor headline, which can diverge from the
    story's representative_title (two clusters of one event can carry near-identical
    representative_titles while their anchor headlines differ)."""
    ids = [int(x) for x in story_ids if str(x).isdigit()]
    if not ids:
        return {}
    rows = db.execute(
        text("SELECT id, representative_title FROM data.stories WHERE id = ANY(:ids)"),
        {"ids": ids},
    ).mappings()
    return {str(r["id"]): (r["representative_title"] or "") for r in rows}


def _depths_for(ordered: list[dict]) -> dict[str, tuple[str, int]]:
    return {o["story_id"]: depth_for_rank(i) for i, o in enumerate(ordered)}


def _ordered_from_ids(db: Session, ids: list[str]) -> list[dict]:
    cats = _categories_for(db, ids)
    return [{"story_id": sid, "primary_category": cats.get(sid)} for sid in ids]


def get_materialised_selection(
    db: Session, *, profile_id: int, request_hash: str, day: date | None = None
) -> tuple[list[dict], dict, int] | None:
    """READ-ONLY: the stored materialised (deduped) selection for today, or None if not built
    yet. Used by the streaming skeleton so it matches the final briefing when the preview has
    already materialised it — without ever running the LLM on the first-play path."""
    day = day or uk_now().date()
    row = db.execute(
        text("""SELECT story_ids, ranking_run_id FROM data.user_daily_editions
                WHERE profile_id = :p AND edition_date = :d AND request_hash = :h
                  AND ranking_run_id IS NOT NULL"""),
        {"p": profile_id, "d": day, "h": request_hash},
    ).first()
    if row is None:
        return None
    ids = row[0] if isinstance(row[0], list) else __import__("json").loads(row[0])
    ids = [str(x) for x in ids]
    ordered = _ordered_from_ids(db, ids)
    return ordered, _depths_for(ordered), int(row[1])


def resolve_materialised_selection(
    db: Session,
    *,
    profile_id: int | None,
    request_hash: str,
    include_top_stories: bool,
    include_categories: list[str] | None,
    exclude_categories: list[str] | None,
    preset: str,
) -> tuple[list[dict], dict, int]:
    """The single source of truth for the preview AND the final briefing. Resolves the pinned
    ranking run (freshness rule: pin for the day; refresh to a newer run only if the user hasn't
    started that briefing yet — no bulletin built for the pinned run), builds+stores the deduped
    selection on first access, and returns (ordered, depths, ranking_run_id).

    profile_id is None (the profile-less /bulletins path): no per-profile edition to pin, so we
    build fresh at the latest run and don't materialise."""
    latest = RankingRunRepo(db).get_latest()
    if latest is None:
        return [], {}, 0
    latest_run = latest["id"]

    if profile_id is None:
        ordered = build_selection(
            db, profile_id=None, include_top_stories=include_top_stories,
            include_categories=include_categories, exclude_categories=exclude_categories, preset=preset,
        )
        return ordered, _depths_for(ordered), latest_run

    day = uk_now().date()
    row = db.execute(
        text("""SELECT story_ids, ranking_run_id FROM data.user_daily_editions
                WHERE profile_id = :p AND edition_date = :d AND request_hash = :h"""),
        {"p": profile_id, "d": day, "h": request_hash},
    ).first()

    pinned_run = None
    if row is not None and row[1] is not None:
        pinned = int(row[1])
        if latest_run <= pinned:
            pinned_run = pinned                       # no newer run → keep
        else:
            started = BulletinRepo(db).get_by_run_and_hash(pinned, request_hash) is not None
            pinned_run = pinned if started else None  # started → keep; else refresh to latest

    if pinned_run is not None:
        ids = row[0] if isinstance(row[0], list) else __import__("json").loads(row[0])
        ordered = _ordered_from_ids(db, [str(x) for x in ids])
        return ordered, _depths_for(ordered), pinned_run

    # First access, or refresh: build (runs the LLM dedup once), pin to latest, store.
    ordered = build_selection(
        db, profile_id=profile_id, include_top_stories=include_top_stories,
        include_categories=include_categories, exclude_categories=exclude_categories, preset=preset,
    )
    UserDailyEditionRepo(db).upsert(profile_id, day, request_hash, [o["story_id"] for o in ordered])
    db.execute(
        text("""UPDATE data.user_daily_editions SET ranking_run_id = :r
                WHERE profile_id = :p AND edition_date = :d AND request_hash = :h"""),
        {"r": latest_run, "p": profile_id, "d": day, "h": request_hash},
    )
    db.commit()
    return ordered, _depths_for(ordered), latest_run
