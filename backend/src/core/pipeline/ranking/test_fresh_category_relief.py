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
    # A non-follower asking for the front page only does NOT get C at all: it is below
    # the front bar, and length is never padded past what's worth hearing (no backfill),
    # so a below-bar trivial story is simply absent.
    front = select_by_thresholds(
        scored, include_top_stories=True, include_categories=None,
        preset="medium", now=NOW,
    )
    assert "C" not in _ids(front)
    # A gaming-FOLLOWER does get it (legitimate) — via_category with the fresh relief.
    follower = select_by_thresholds(
        scored, include_top_stories=False, include_categories=["technology.gaming"],
        preset="medium", now=NOW,
    )
    assert "C" in _ids(follower)


def test_combined_bulletin_ordering_stays_coverage_first():
    """A follower of both sport and gaming: the briefing contains ONLY sport + gaming,
    ordered coverage-first, with the fresh single-source items filling tail slots and
    never leapfrogging a bigger story. The big war (D, politics.world) is OUTSIDE the
    selection, so it must NOT appear — even though the app sends include_top_stories=true
    on every request. (Regression guard for the reported bug: a narrow selection was
    getting unselected hard news injected via the always-on front-page flag.)"""
    scored = list(_scored().values())
    rows = select_by_thresholds(
        scored, include_top_stories=True,
        include_categories=["sport", "technology.gaming"],
        preset="detailed", now=NOW,
    )
    assert _ids(rows) == ["B", "A", "C"]           # only selected cats; D excluded
    assert "D" not in _ids(rows)


def test_front_page_composition_unaffected_by_relief():
    """via_front is byte-identical whether stories are fresh or not — the relief
    only ever touches via_category."""
    scored = list(_scored().values())
    fresh = select_by_thresholds(
        scored, include_top_stories=True, include_categories=None,
        preset="medium", now=NOW,
    )
    inert = select_by_thresholds(
        scored, include_top_stories=True, include_categories=None,
        preset="medium", now=NOW + timedelta(days=10),
    )
    assert _ids(fresh) == _ids(inert)
