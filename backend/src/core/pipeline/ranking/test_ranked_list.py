"""
Stable ranked-list editorial re-stamp (audit §1 / §7-B.10 / risk #1).

The stable list stops rank CHURN but must NOT freeze editorial VERDICTS. top_story is a
front-page GATE (bypasses the bar and all caps), so a flag frozen at insert — while the
editor later reverses it — is maximally consequential (live: story 5074 led the front page
on a 14:15 flag the editor flipped False from 18:30 on).

These pin the flag logic on the read_from_ranked_list=TRUE path (the one prod runs): the
per-story top_story flag equals the LATEST editorial verdict every reviewed run, independent
of coverage growth, and merit/positions are untouched by that re-stamp. Pure — no DB, in the
style of test_threshold_filter_order.py; restamp_top_story is the bulk-SQL equivalent of
top_story_flags.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import ScoredStory, StoryRankingCandidate
from .ranked_list import top_story_flags
from .thresholds import qualify_and_order

NOW = datetime(2026, 7, 7, 19, 0, tzinfo=timezone.utc)


def _ss(sid: str, cat: str, score: float, *, geo: str | None = "us", top: bool = False,
        source_count: int = 12) -> ScoredStory:
    c = StoryRankingCandidate(
        story_id=sid, title=sid, last_seen_at=NOW - timedelta(hours=5),
        article_count=3, source_count=source_count, primary_category=cat,
        primary_entity_id=None, primary_entity_name=None, primary_entity_weight=None,
        primary_topic_key=None, geo_region=geo, pool_country="us", sources=(), top_story=top,
    )
    return ScoredStory(
        candidate=c, within_category_score=0.0, full_score=0.0, category_weight=1.0,
        recency_score=0.0, entity_weight=1.0, source_weight=1.0, cluster_weight=1.0,
        importance=0.0, normalized_score=score,
    )


def _front_page(stories: list[ScoredStory]) -> list[str]:
    """Story ids admitted to the bare all-topics front page, in order."""
    out = qualify_and_order(stories, include_top_stories=True, include_categories=None, now=NOW)
    return [s.candidate.story_id for s in out]


def test_editor_flip_false_no_growth_flips_flag_and_drops_front_page():
    # Story 5074: placed & leading with top_story=True, source_count frozen at 12 (no growth).
    # This run's editorial no longer flags it (verdict reversed). The flag MUST flip without
    # any coverage change, and the story must stop qualifying for the front page.
    placed = {"5074", "keep"}
    flagged_now = {"keep"}                        # editor flags 'keep', not 5074, this run
    flags = top_story_flags(placed, flagged_now)
    assert flags["5074"] is False                 # re-stamped to the current verdict
    assert flags["keep"] is True

    lead = _ss("5074", "politics.us", 8.96, top=True)      # what the list serves TODAY (stale)
    demoted = _ss("5074", "politics.us", 8.96, top=flags["5074"])  # after the re-stamp
    assert _front_page([lead]) == ["5074"]        # stale flag → still leads (the bug)
    assert _front_page([demoted]) == []           # re-stamped → no longer front-page qualifies


def test_late_promotion_appears_without_coverage_growth():
    # A story placed with top_story=False that the editor promotes on a LATER run — no coverage
    # change. The flag must appear from the current verdict, and (being a gate, not a score) it
    # leads the front page despite low merit.
    placed = {"late"}
    flags = top_story_flags(placed, flagged={"late"})
    assert flags["late"] is True
    promoted = _ss("late", "politics.us", 5.0, top=flags["late"])   # low merit, now flagged
    assert _front_page([promoted]) == ["late"]                      # qualifies via the flag


def test_no_change_run_is_order_stable():
    # Identical verdict on consecutive runs → identical flags and byte-identical ordering.
    # (top_story never enters the ORDER BY merit_score DESC, story_id key, so re-stamping a
    # story's gate cannot move any position.)
    pool = [_ss("a", "politics.us", 8.0, top=True),
            _ss("b", "politics.us", 7.0, top=False),
            _ss("c", "politics.us", 6.0, top=False)]
    flagged = {"a"}

    def apply(verdict: set[str]) -> list[str]:
        f = top_story_flags({s.candidate.story_id for s in pool}, verdict)
        for s in pool:
            s.candidate.top_story = f[s.candidate.story_id]
        return _front_page(pool)

    assert top_story_flags({s.candidate.story_id for s in pool}, flagged) == \
           top_story_flags({s.candidate.story_id for s in pool}, flagged)  # deterministic
    order_1 = apply(flagged)
    order_2 = apply(flagged)                         # unchanged verdict
    assert order_1 == order_2                        # order preserved


def test_flag_depends_only_on_verdict_not_coverage():
    # The re-stamp is decoupled from source_count entirely: same story, wildly different
    # coverage, flag is a pure function of the editorial flagged set.
    assert top_story_flags({"x"}, {"x"}) == {"x": True}
    assert top_story_flags({"x"}, set()) == {"x": False}
