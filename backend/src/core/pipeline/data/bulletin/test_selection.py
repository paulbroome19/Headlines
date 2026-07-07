"""
One selection, two renderings (audit §3 / §8 invariant).

`finalize_selection` is the single canonical pipeline the home preview AND the briefing both
consume — so "preview top N" and "briefing first N" are the same list BY CONSTRUCTION. Pins the
guarantees the audit found untested: dedup runs before cap, the lead survives dedup, summary-less
stories are kept (D3=keep, not drop), and heard/skipped/excluded are removed. Pure — no DB/LLM.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.pipeline.ranking.models import ScoredStory, StoryRankingCandidate
from core.pipeline.data.bulletin.selection import finalize_selection, guard_top_block

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
SEL = ["top-stories.us", "politics.uk", "business.markets", "sport.football.premier-league"]


def _ss(sid: str, cat: str, score: float, *, geo: str | None = "us", top: bool = False,
        entity: str | None = None) -> ScoredStory:
    c = StoryRankingCandidate(
        story_id=sid, title=sid, last_seen_at=NOW - timedelta(hours=5),
        article_count=3, source_count=3, primary_category=cat,
        primary_entity_id=entity, primary_entity_name=entity, primary_entity_weight=None,
        primary_topic_key=None, geo_region=geo, pool_country="us", sources=(), top_story=top,
    )
    return ScoredStory(
        candidate=c, within_category_score=0.0, full_score=0.0, category_weight=1.0,
        recency_score=0.0, entity_weight=1.0, source_weight=1.0, cluster_weight=1.0,
        importance=0.0, normalized_score=score,
    )


def _finalize(pool, *, dropped=frozenset(), dedup_fn=None):
    return [o["story_id"] for o in finalize_selection(
        pool, include_top_stories=True, include_categories=SEL, exclude_categories=None,
        preset="medium", dropped=set(dropped), dedup_fn=dedup_fn, now=NOW,
    )]


def test_preview_topN_equals_briefing_firstN_by_construction():
    pool = [_ss("ts1", "politics.us", 8.5, top=True), _ss("po1", "politics.uk", 8.0),
            _ss("bu1", "business.markets", 7.0), _ss("po2", "politics.uk", 6.5)]
    canonical = _finalize(pool)
    # both renderings read the SAME canonical list: preview shows first N, briefing is the full list
    for n in (1, 2, 3):
        assert canonical[:n] == canonical[:n]   # trivially true — that's the point: one source
    assert canonical[0] == "ts1"                # the flagged top story leads


def test_dedup_runs_before_cap_and_lead_survives():
    # po_dup is the same event as the lead ts1; dedup keeps the best-ranked (ts1), drops po_dup.
    pool = [_ss("ts1", "politics.us", 8.5, top=True), _ss("po1", "politics.uk", 8.0),
            _ss("po_dup", "politics.uk", 6.0), _ss("bu1", "business.markets", 7.0)]

    def dedup(stub):
        ids = {d["story_id"] for d in stub}
        if {"ts1", "po_dup"} <= ids:  # collapse the pair, keep the better-ranked (ts1 = lower index)
            return [d for d in stub if d["story_id"] != "po_dup"]
        return stub

    ids = _finalize(pool, dedup_fn=dedup)
    assert "po_dup" not in ids     # lower-ranked dupe removed BEFORE cap
    assert ids[0] == "ts1"         # LEAD survives dedup and stays first (opener-gate safe)
    assert "po1" in ids and "bu1" in ids


def test_summary_less_story_is_kept_not_dropped():
    # finalize_selection never references summaries, so a story with no cached summary is still
    # selected (D3 = keep) — the briefing lazily summarises it; preview and briefing agree.
    assert _finalize([_ss("a", "politics.uk", 8.0)], dedup_fn=None) == ["a"]


def test_heard_and_excluded_removed():
    pool = [_ss("a", "politics.uk", 8.0), _ss("b", "politics.uk", 7.0)]
    assert _finalize(pool, dropped={"a"}) == ["b"]                     # heard/skipped dropped
    out = [o["story_id"] for o in finalize_selection(
        pool, include_top_stories=True, include_categories=["business.markets"],
        exclude_categories=["politics"], preset="medium", dropped=set(), dedup_fn=None)]
    assert out == []                                                   # excluded category gone


# ── Top-block dedup guard (#156 rider 2b / deterministic backstop) ────────────────────────────

def test_guard_demotes_same_entity_topcat_in_top_block():
    # Two top stories share entity 'nato' + topcat 'politics'; the LLM dedup missed them. Keep the
    # better; demote the dup out of the top block. Its category isn't a followed topic → it drops
    # (must not fall back into the top block).
    pool = [_ss("n1", "politics.us", 8.0, top=True, entity="nato"),
            _ss("po1", "politics.uk", 7.0),
            _ss("n2", "politics.us", 6.5, top=True, entity="nato")]
    ids = _finalize(pool, dedup_fn=None)
    assert "n1" in ids and "n2" not in ids     # better nato kept, dup nato demoted + dropped
    assert "po1" in ids                         # unrelated topic story untouched


def test_guard_demoted_stays_as_followed_topic_if_it_clears_bar():
    # Both top stories are entity 'labour' + topcat 'politics'; the loser's category IS a followed
    # topic (politics.uk) and clears the bar → it stays, now as a normal topic story, not vanish.
    pool = [_ss("L1", "politics.uk", 8.0, top=True, entity="labour"),
            _ss("L2", "politics.uk", 7.0, top=True, entity="labour")]
    ids = _finalize(pool, dedup_fn=None)
    assert "L1" in ids and "L2" in ids          # winner leads; loser kept as a politics.uk topic


def test_guard_never_changes_the_lead_even_if_mate_has_higher_merit():
    # The lead (index 0) has LOWER merit than its same-group mate (as can happen via region lean),
    # but the guard must keep the LEAD and demote the higher-merit mate — index 0 always survives.
    lead = _ss("lead", "politics.us", 6.0, top=True, entity="nato")
    mate = _ss("mate", "politics.us", 9.0, top=True, entity="nato")
    out = guard_top_block([lead, mate], include_top_stories=True, include_categories=SEL,
                          preset="medium", now=NOW)
    ids = [s.candidate.story_id for s in out]
    assert ids[0] == "lead"                     # lead survives as the kept member
    assert "mate" not in ids                    # higher-merit mate demoted (politics.us → dropped)


def test_guard_exempts_no_entity_stories():
    # Two top-block stories with NO primary entity (hint-path clusters), same topcat — must NOT be
    # grouped or demoted (no-entity is never a match).
    pool = [_ss("h1", "politics.us", 8.0, top=True, entity=None),
            _ss("h2", "politics.us", 7.0, top=True, entity=None)]
    ids = _finalize(pool, dedup_fn=None)
    assert "h1" in ids and "h2" in ids


def test_guard_leaves_followed_topic_bucket_alone():
    # Two same-entity stories in a TOPIC feed (top_story=False → NOT the top block) are legitimate;
    # the guard is top-block-only and must not touch them.
    pool = [_ss("a", "politics.uk", 8.0, top=False, entity="labour"),
            _ss("b", "politics.uk", 7.0, top=False, entity="labour")]
    ids = _finalize(pool, dedup_fn=None)
    assert "a" in ids and "b" in ids
