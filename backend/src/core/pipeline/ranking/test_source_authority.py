"""
Curated source authority (PR1): resolve_authority + the scorer/importance wiring.

Pins: global Western core leads everywhere; specialists get a home-category boost
but still count elsewhere; the regional whitelist only activates when its region is
selected; excluded outlets (Times of Israel / Jerusalem Post) never confer authority;
unknown-only stories are hard-down-ranked (×UNKNOWN_AUTHORITY_FACTOR) and never
lead-eligible; and the gb UK lean is applied in country_weight.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.platform.config.source_credibility import resolve_authority
from core.pipeline.data.ingest.pool_taxonomy import country_weight

from .category_ranker import get_category_weight
from .config import SOURCE_AUTHORITY_STRENGTH
from .models import ScoredStory, StoryRankingCandidate
from .scorer import score_story, story_authority

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)


def _cand(sid: str, cat: str, sources: tuple[str, ...], src: int = 1) -> StoryRankingCandidate:
    return StoryRankingCandidate(
        story_id=sid,
        title=sid,
        last_seen_at=NOW - timedelta(hours=4),
        article_count=src,
        source_count=src,
        primary_category=cat,
        primary_entity_id=None,
        primary_entity_name=None,
        primary_entity_weight=None,
        primary_topic_key=None,
        geo_region=None,
        pool_country="gb",
        sources=sources,
    )


def _score(c: StoryRankingCandidate) -> ScoredStory:
    return score_story(c, get_category_weight(c.primary_category), now=NOW)


# ── resolver ─────────────────────────────────────────────────────────────────

def test_global_core_trusted_everywhere():
    for outlet in ("BBC", "Fox News", "NBC News", "CNN", "The Guardian", "AP News"):
        bonus, lead = resolve_authority([outlet], "politics.world")
        assert (bonus, lead) == (1.0, True), outlet


def test_specialist_home_beats_away_but_both_lead():
    home, hlead = resolve_authority(["TechCrunch"], "technology.ai")
    away, alead = resolve_authority(["TechCrunch"], "politics.uk")
    assert hlead and alead
    assert home > away > 0.0


def test_unknown_and_excluded_not_trusted():
    for outlet in ("Mirage News", "Moneycontrol.com", "Modern Ghana"):
        assert resolve_authority([outlet], "science") == (0.0, False)
    # Excluded even when its region is selected.
    assert resolve_authority(["The Jerusalem Post"], "politics.world", {"middle-east"}) == (0.0, False)
    assert resolve_authority(["Times of Israel"], "politics.world", {"middle-east"}) == (0.0, False)


def test_regional_gated_by_selected_region():
    # Absent from a general (no-region) bulletin…
    assert resolve_authority(["Al Jazeera"], "politics.world") == (0.0, False)
    assert resolve_authority(["Daily Maverick"], "politics.world") == (0.0, False)
    # …trusted only when their region is explicitly selected.
    assert resolve_authority(["Al Jazeera"], "politics.world", {"middle-east"})[1] is True
    assert resolve_authority(["Daily Maverick"], "politics.world", {"africa"})[1] is True
    # Wrong region selected → still not trusted.
    assert resolve_authority(["Daily Maverick"], "politics.world", {"asia"}) == (0.0, False)


def test_best_outlet_wins_and_excluded_does_not_poison():
    # A story with BBC + an unknown is trusted via BBC.
    assert resolve_authority(["Modern Ghana", "BBC"], "politics.world") == (1.0, True)
    # Excluded alongside a trusted regional (region selected) → regional wins.
    b, l = resolve_authority(["The Jerusalem Post", "Al Jazeera"], "politics.world", {"middle-east"})
    assert l is True and b > 0.0


# ── scorer / importance wiring ───────────────────────────────────────────────

def test_trusted_outranks_unknown_same_coverage():
    trusted = _score(_cand("t", "politics.world", ("BBC",)))
    unknown = _score(_cand("u", "politics.world", ("Mirage News",)))
    assert trusted.importance > unknown.importance
    assert trusted.lead_eligible is True
    assert unknown.lead_eligible is False


def test_authority_bonus_and_boost():
    # PR1 applies the trusted BONUS lift on the importance axis; unknown gets no lift
    # here (the hard unknown down-rank lives in thresholds.py / PR2).
    b_bonus, b_lead = story_authority(_cand("t", "politics.world", ("BBC",)))
    u_bonus, u_lead = story_authority(_cand("u", "politics.world", ("Mirage News",)))
    assert (b_bonus, b_lead) == (1.0, True)
    assert (u_bonus, u_lead) == (0.0, False)
    t = _score(_cand("t", "politics.world", ("BBC",)))
    u = _score(_cand("u", "politics.world", ("Mirage News",)))
    # trusted importance is exactly (1 + strength)× the unknown's, same coverage/category.
    assert abs(t.importance / u.importance - (1.0 + SOURCE_AUTHORITY_STRENGTH)) < 1e-9


def test_uk_lean_country_weight():
    assert country_weight("gb") == 1.3
    assert country_weight("us") == 1.0
    assert country_weight("ke") == 0.6
    assert country_weight(None) == 0.3
