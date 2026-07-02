"""
Newspaper tier model: category sets the front-page tier (T1 front / T2 middle /
T3 back), coverage orders WITHIN a tier, and a soft (T2/T3) story breaks through to
the front ONLY on exceptional distinct-source coverage (>= BREAKTHROUGH_SOURCES).

Tier is DECISIVE — it must not be overwhelmed by soft-news coverage the way the old
±14% category tiebreak was. These tests pin the resolver, the break-through bar, and
the end-to-end front-page ordering (the reported failure: four 8–10-source football
stories leading the front page).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .category_ranker import get_category_tier, get_category_weight
from .config import BREAKTHROUGH_SOURCES
from .models import ScoredStory, StoryRankingCandidate
from .scorer import score_story
from .thresholds import effective_tier, select_by_thresholds

NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)


def _cand(sid: str, src: int, cat: str, hours_ago: float = 4.0) -> StoryRankingCandidate:
    return StoryRankingCandidate(
        story_id=sid,
        title=sid,
        last_seen_at=NOW - timedelta(hours=hours_ago),
        article_count=src,
        source_count=src,
        primary_category=cat,
        primary_entity_id=None,
        primary_entity_name=None,
        primary_entity_weight=None,
        primary_topic_key=None,
        geo_region=None,
        pool_country="gb",
        top_source_tier=9,
    )


def _score(c: StoryRankingCandidate) -> ScoredStory:
    return score_story(c, get_category_weight(c.primary_category), now=NOW)


# ── resolver: exact / prefix / default ───────────────────────────────────────

def test_tier_resolution():
    assert get_category_tier("top-stories") == 1
    assert get_category_tier("top-stories.middle-east") == 1     # prefix
    assert get_category_tier("politics.world") == 1              # prefix (geopolitics is front)
    assert get_category_tier("business.markets") == 1
    assert get_category_tier("world") == 2
    assert get_category_tier("health") == 2
    assert get_category_tier("science") == 2
    assert get_category_tier("technology.gaming") == 3           # prefix
    assert get_category_tier("sport.football.premier-league") == 3
    assert get_category_tier("culture.celebrity") == 3
    assert get_category_tier(None) == 3                          # default
    assert get_category_tier("made.up.slug") == 3               # default


# ── break-through: ordinary soft news never breaks; a mega-story does ─────────

def test_ordinary_football_does_not_break_through():
    # 10 sources — the real high-water mark for football clustering this morning.
    assert effective_tier(_score(_cand("f", 10, "sport.football.internationals"))) == 3


def test_exceptional_coverage_breaks_through():
    mega = _score(_cand("wc-final", BREAKTHROUGH_SOURCES, "sport.football.internationals"))
    assert effective_tier(mega) == 1
    # one below the bar stays at the back
    assert effective_tier(_score(_cand("almost", BREAKTHROUGH_SOURCES - 1, "sport"))) == 3


def test_tier1_never_demoted():
    # A thin single-source hard-news story is still Tier 1 (category is decisive).
    assert effective_tier(_score(_cand("p", 1, "politics.world"))) == 1


# ── end-to-end: the reported failure is fixed ────────────────────────────────

def test_front_page_leads_with_hard_news_not_football():
    # The reported failure: four 8–10-source football stories + a gaming story
    # leading, ahead of thinner-covered but higher-tier hard news.
    scored = [
        _score(_cand("gaming", 27, "technology.gaming")),          # T3, huge but < bar
        _score(_cand("foot1", 10, "sport.football.internationals")),
        _score(_cand("foot2", 9, "sport.football.premier-league")),
        _score(_cand("foot3", 8, "sport.football.internationals")),
        _score(_cand("trump", 5, "politics.us")),                  # T1, thinner coverage
        _score(_cand("fed", 5, "business.economy")),               # T1
        _score(_cand("iran", 4, "top-stories.middle-east")),       # T1
    ]
    sel = select_by_thresholds(
        scored, include_top_stories=True, include_categories=None,
        preset="medium", target_count=10, now=NOW,
    )
    tiers = [effective_tier(s) for s in sel]
    assert tiers == sorted(tiers)                                  # tier is decisive
    lead_cats = [s.candidate.primary_category for s in sel[:3]]
    assert all(get_category_tier(c) == 1 for c in lead_cats)       # hard news leads
    # the soft stories are present (never-empty preserved) but sink to the back
    ids = [s.candidate.story_id for s in sel]
    for soft in ("gaming", "foot1", "foot2", "foot3"):
        assert ids.index(soft) > ids.index("trump")


def test_a_true_mega_story_can_lead():
    scored = [
        _score(_cand("wc-final", 30, "sport.football.internationals")),  # breaks through
        _score(_cand("trump", 8, "politics.us")),
    ]
    sel = select_by_thresholds(
        scored, include_top_stories=True, include_categories=None,
        preset="medium", target_count=10, now=NOW,
    )
    # Both Tier 1 now; the mega-story leads on coverage within the tier.
    assert sel[0].candidate.story_id == "wc-final"


def test_sport_follower_still_gets_sport():
    scored = [
        _score(_cand("foot1", 10, "sport.football.internationals")),
        _score(_cand("foot2", 6, "sport.football.premier-league")),
    ]
    sel = select_by_thresholds(
        scored, include_top_stories=False, include_categories=["sport"],
        preset="medium", target_count=10, now=NOW,
    )
    assert {s.candidate.story_id for s in sel} == {"foot1", "foot2"}
