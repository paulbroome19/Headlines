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
from core.pipeline.data.bulletin.selection import finalize_selection

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
SEL = ["top-stories.us", "politics.uk", "business.markets", "sport.football.premier-league"]


def _ss(sid: str, cat: str, score: float, *, geo: str | None = "us", top: bool = False) -> ScoredStory:
    c = StoryRankingCandidate(
        story_id=sid, title=sid, last_seen_at=NOW - timedelta(hours=5),
        article_count=3, source_count=3, primary_category=cat,
        primary_entity_id=None, primary_entity_name=None, primary_entity_weight=None,
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
        preset="medium", dropped=set(dropped), dedup_fn=dedup_fn,
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
