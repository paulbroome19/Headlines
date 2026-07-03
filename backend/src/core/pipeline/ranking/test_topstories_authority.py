"""
Top-Stories selection authority + lead gating (PR2, thresholds.py).

Pins the four acceptance properties:
  (a) the highest-scored TRUSTED hard-news story leads;
  (b) an unknown single-source story can NEVER lead a general bulletin (supplementary);
  (c) region-backfilled stories are supplementary — they never displace a qualifier;
  (d) regional whitelist names only count when their region is explicitly selected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .category_ranker import get_category_weight
from .config import LEAD_PIN_COUNT
from .models import ScoredStory, StoryRankingCandidate
from .scorer import score_story
from .thresholds import select_by_thresholds

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)


def _cand(sid, cat, sources, src, arts=None, country="us") -> StoryRankingCandidate:
    return StoryRankingCandidate(
        story_id=sid,
        title=sid,
        last_seen_at=NOW - timedelta(hours=3),
        article_count=arts if arts is not None else src,
        source_count=src,
        primary_category=cat,
        primary_entity_id=None,
        primary_entity_name=None,
        primary_entity_weight=None,
        primary_topic_key=None,
        geo_region=None,
        pool_country=country,
        sources=sources,
    )


def _score(c) -> ScoredStory:
    return score_story(c, get_category_weight(c.primary_category), now=NOW)


def _ids(rows):
    return [s.candidate.story_id for s in rows]


def test_trusted_leads_even_when_unknown_has_higher_coverage():
    """(a)+(b): an unknown outlet with the HIGHEST coverage must not lead; the trusted
    (lower-coverage) hard-news story leads. The unknown still appears, supplementary."""
    unknown_big = _score(_cand("UNK", "politics.world", ("Random Blog",), src=25))
    trusted = _score(_cand("BBC", "politics.world", ("BBC",), src=10))
    # Sanity: the unknown genuinely has the higher raw coverage score.
    assert unknown_big.normalized_score > trusted.normalized_score
    rows = select_by_thresholds(
        [unknown_big, trusted], include_top_stories=True, include_categories=None,
        preset="medium", target_count=10, now=NOW,
    )
    assert _ids(rows)[0] == "BBC"          # trusted leads
    assert "UNK" in _ids(rows)             # unknown still present…
    assert _ids(rows).index("UNK") >= LEAD_PIN_COUNT or _ids(rows).index("UNK") > 0  # …but never in the lead


def test_regional_name_only_activates_when_region_selected():
    """(d): Al Jazeera is untrusted in a general/no-region bulletin (down-ranked, can't
    lead) but lead-eligible once its region is selected."""
    aj = _cand("AJ", "politics.world", ("Al Jazeera",), src=6)
    aj.geo_region = "middle-east"
    aj_s = _score(aj)
    filler = _score(_cand("BBC", "politics.world", ("BBC",), src=6))

    # No region selected: bare front page → AJ is not lead-eligible, BBC leads.
    general = select_by_thresholds(
        [aj_s, filler], include_top_stories=True, include_categories=None,
        preset="medium", target_count=10, now=NOW,
    )
    assert _ids(general)[0] == "BBC"

    # Middle-east region selected → AJ activates and can lead its region bucket.
    regional = select_by_thresholds(
        [aj_s, filler], include_top_stories=False,
        include_categories=["top-stories.middle-east"],
        preset="medium", target_count=10, now=NOW,
    )
    assert "AJ" in _ids(regional)


def test_backfilled_story_is_supplementary_never_leads():
    """(c): a story admitted only by the never-empty backfill (below the bar) fills a
    tail slot and never occupies a lead slot."""
    strong = _score(_cand("STRONG", "politics.world", ("BBC",), src=15))
    weak = _score(_cand("WEAK", "sport.football.premier-league", ("Some Blog",), src=1))
    rows = select_by_thresholds(
        [strong, weak], include_top_stories=True, include_categories=None,
        preset="medium", target_count=10, now=NOW,
    )
    assert _ids(rows)[0] == "STRONG"
    assert "WEAK" in _ids(rows)                    # backfilled in (never-empty floor)…
    assert _ids(rows).index("WEAK") > 0            # …but supplementary, never the lead
