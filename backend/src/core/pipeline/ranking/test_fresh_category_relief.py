"""
Fresh followed-topic category-bar relief (Change 2 of the freshness proposal).

A genuinely-breaking story arrives single-source, so coverage (the dominant
importance term) can't yet rate it highly — it sits just under its category bar
until other outlets pick it up. The relief lets such a story clear its OWN
(already-lower) category bar a little sooner, decaying ~45-min e-fold. It is
applied to the via_category branch ONLY: the front-page (via_front / via_region)
bars and the final coverage-first sort are untouched by construction.

The four scenarios are the proposal's worked examples:
  A — England win, single-source, fresh, sport      → score 6.70
  B — England win, 4 sources, fresh, sport           → score 7.86
  C — trivial gaming blog, single-source, fresh      → score 6.40
  D — big war, 15 sources, 3 h old, politics.world   → score 8.81
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .category_ranker import get_category_weight
from .config import FRESH_CATEGORY_RELIEF, THRESHOLDS
from .models import ScoredStory, StoryRankingCandidate
from .scorer import score_story
from .thresholds import select_by_thresholds

NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)


def _cand(sid: str, src: int, arts: int, mins_ago: int, cat: str, tier: int) -> StoryRankingCandidate:
    return StoryRankingCandidate(
        story_id=sid,
        title=sid,
        last_seen_at=NOW - timedelta(minutes=mins_ago),
        article_count=arts,
        source_count=src,
        primary_category=cat,
        primary_entity_id=None,
        primary_entity_name=None,
        primary_entity_weight=None,
        primary_topic_key=None,
        geo_region=None,
        pool_country="gb",
        top_source_tier=tier,
    )


def _scored() -> dict[str, ScoredStory]:
    cands = {
        "A": _cand("A", 1, 1, 6, "sport.football.premier-league", 1),
        "B": _cand("B", 4, 5, 18, "sport.football.premier-league", 1),
        "C": _cand("C", 1, 1, 6, "technology.gaming", 9),
        "D": _cand("D", 15, 25, 180, "politics.world", 1),
    }
    return {
        sid: score_story(c, get_category_weight(c.primary_category), now=NOW)
        for sid, c in cands.items()
    }


def _ids(rows: list[ScoredStory]) -> list[str]:
    return [s.candidate.story_id for s in rows]


def test_relief_constant_within_safe_bound():
    # Above ~0.5 the relief offsets the coverage deficit itself (not just the fresh
    # single-source gap) and weak noise leaks into follower lists.
    assert FRESH_CATEGORY_RELIEF <= 0.5


def test_baseline_scores_are_coverage_ordered():
    s = _scored()
    assert s["D"].normalized_score > s["B"].normalized_score > s["A"].normalized_score > s["C"].normalized_score


def test_A_sport_follower_qualifies_on_every_preset():
    """England single-source is admitted for a sport-follower on Quick/Standard/Deep."""
    scored = list(_scored().values())
    for preset in ("short", "medium", "detailed"):
        rows = select_by_thresholds(
            scored, include_top_stories=False, include_categories=["sport"],
            preset=preset, now=NOW,
        )
        assert "A" in _ids(rows), f"A missing on {preset}"


def test_A_on_quick_requires_the_relief():
    """On Quick, England single-source clears ONLY because of the relief: with the
    relief decayed to ~0 (story no longer fresh) it drops below the 7.0 category bar."""
    scored = list(_scored().values())
    # Baseline score is genuinely under the Quick category bar...
    assert _scored()["A"].normalized_score < THRESHOLDS["short"]["category"]
    # ...admitted while fresh...
    fresh = select_by_thresholds(
        scored, include_top_stories=False, include_categories=["sport"],
        preset="short", now=NOW,
    )
    assert "A" in _ids(fresh)
    # ...but NOT once the relief has decayed away (evaluate far in the future).
    stale = select_by_thresholds(
        scored, include_top_stories=False, include_categories=["sport"],
        preset="short", now=NOW + timedelta(days=10),
    )
    assert "A" not in _ids(stale)


def test_C_trivial_gaming_only_for_follower_never_front_page():
    scored = list(_scored().values())
    cs = _scored()["C"]
    # Never reaches a front-page (top_stories) bar on any preset → via_front can't fire.
    for preset in ("short", "medium", "detailed"):
        assert cs.normalized_score < THRESHOLDS[preset]["top_stories"]
    # A non-follower asking for the front page only doesn't get C on merit: it is
    # below the front bar, so it can appear solely via the backfill floor — which is
    # coverage-ordered, so it lands LAST.
    front = select_by_thresholds(
        scored, include_top_stories=True, include_categories=None,
        preset="medium", target_count=10, now=NOW,
    )
    assert _ids(front)[-1] == "C"
    # A gaming-FOLLOWER does get it (legitimate) — via_category with the fresh relief.
    follower = select_by_thresholds(
        scored, include_top_stories=False, include_categories=["technology.gaming"],
        preset="medium", now=NOW,
    )
    assert "C" in _ids(follower)


def test_combined_bulletin_ordering_stays_coverage_first():
    """A follower of both sport and gaming: big news leads; the fresh single-source
    items fill tail slots and never leapfrog a bigger story."""
    scored = list(_scored().values())
    rows = select_by_thresholds(
        scored, include_top_stories=True,
        include_categories=["sport", "technology.gaming"],
        preset="detailed", target_count=10, now=NOW,
    )
    assert _ids(rows) == ["D", "B", "A", "C"]


def test_front_page_composition_unaffected_by_relief():
    """via_front is byte-identical whether stories are fresh or not — the relief
    only ever touches via_category."""
    scored = list(_scored().values())
    fresh = select_by_thresholds(
        scored, include_top_stories=True, include_categories=None,
        preset="medium", target_count=10, now=NOW,
    )
    inert = select_by_thresholds(
        scored, include_top_stories=True, include_categories=None,
        preset="medium", target_count=10, now=NOW + timedelta(days=10),
    )
    assert _ids(fresh) == _ids(inert)
