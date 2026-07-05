"""
Threshold + filter-order selection (replaces count-range + newspaper tiers).

Pins the model's guarantees on synthetic ScoredStories (pure — no DB/scorer):
  - qualify by the preset BAR (below-bar excluded; unselected categories never admitted);
  - RUNNING ORDER = filter sequence (top-stories → politics → business → … → sport last),
    within a category by rank;
  - round-robin DEPTH per category + global CEILING;
  - football appears for a football follower (the bug this replaces);
  - presets yield DISTINCT sizes; thin categories are absent, never padded;
  - fresh-followed-topic relief still clears a just-under-bar breaking story.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .category_ranker import filter_category_index
from .category_ranker import filter_category_index
from .config import MAX_BULLETIN_STORIES, PRESET_DEPTH
from .models import ScoredStory, StoryRankingCandidate
from .thresholds import _bucket_index, _selection_context, cap_bulletin, qualify_and_order

_, _, _TOPIC = _selection_context(True, ["top-stories.us", "politics.uk", "business.markets", "sport.football.premier-league"])

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)

# A football + hard-news follower: top-stories(US) + UK politics + markets + Premier League.
SEL = ["top-stories.us", "politics.uk", "business.markets", "sport.football.premier-league"]


def _ss(sid: str, cat: str, score: float, *, geo: str | None = None, hours_ago: float = 5.0) -> ScoredStory:
    c = StoryRankingCandidate(
        story_id=sid, title=sid, last_seen_at=NOW - timedelta(hours=hours_ago),
        article_count=3, source_count=3, primary_category=cat,
        primary_entity_id=None, primary_entity_name=None, primary_entity_weight=None,
        primary_topic_key=None, geo_region=geo, pool_country="us", sources=(),
    )
    return ScoredStory(
        candidate=c, within_category_score=0.0, full_score=0.0, category_weight=1.0,
        recency_score=0.0, entity_weight=1.0, source_weight=1.0, cluster_weight=1.0,
        importance=0.0, normalized_score=score,
    )


def _pool() -> list[ScoredStory]:
    return [
        _ss("ts1", "top-stories.us", 8.0, geo="us"), _ss("ts2", "top-stories.us", 7.5, geo="us"),
        _ss("ts3", "top-stories.us", 6.5, geo="us"),
        _ss("po1", "politics.uk", 8.6), _ss("po2", "politics.uk", 7.0), _ss("po3", "politics.uk", 6.2),
        _ss("po_lo", "politics.uk", 5.0),                         # below every bar
        _ss("bu1", "business.markets", 7.0), _ss("bu2", "business.markets", 6.5),
        _ss("fb1", "sport.football.premier-league", 8.5), _ss("fb2", "sport.football.premier-league", 7.0),
        _ss("fb3", "sport.football.premier-league", 6.1),
        _ss("cu1", "culture.tv-film", 9.0),                       # UNSELECTED + no region → never appears
        _ss("he1", "health", 7.7, geo="us"),                     # off-topic but a US top story → top-stories block
    ]


def _q(preset: str) -> list[ScoredStory]:
    return qualify_and_order(_pool(), include_top_stories=True, include_categories=SEL, preset=preset, now=NOW)


def _cap(preset: str) -> list[ScoredStory]:
    return cap_bulletin(_q(preset), include_top_stories=True, include_categories=SEL, preset=preset)


def _tops(rows):
    return [(r.candidate.primary_category or "").split(".")[0] for r in rows]


def test_bar_excludes_below_and_unselected():
    ids = {r.candidate.story_id for r in _q("detailed")}   # lowest bar (6.0)
    assert "po_lo" not in ids            # 5.0 — below the bar
    assert "cu1" not in ids              # culture — not selected, however high its score


def test_running_order_is_filter_sequence_sport_last():
    rows = _cap("detailed")
    buckets = [_bucket_index(r.candidate.primary_category, _TOPIC) for r in rows]
    assert buckets == sorted(buckets)               # non-decreasing SELECTED-source bucket
    assert buckets[0] == 0                           # top-stories block leads
    assert buckets[-1] == filter_category_index("sport")  # sport bucket last, by design


def test_region_pulls_offtopic_story_into_topstories_block():
    # A health story (not a followed topic) that is a big US story qualifies via the
    # top-stories.us region and sits in the front TOP-STORIES block — NOT a health section.
    rows = _cap("detailed")
    he = next(r for r in rows if r.candidate.story_id == "he1")
    assert _bucket_index(he.candidate.primary_category, _TOPIC) == 0     # top-stories block
    # There is no standalone "health" bucket in the running order.
    assert all(_bucket_index(r.candidate.primary_category, _TOPIC) != filter_category_index("health") for r in rows)


def test_within_category_ordered_by_rank():
    rows = _cap("detailed")
    pol = [r.normalized_score for r in rows if (r.candidate.primary_category or "").startswith("politics")]
    assert pol == sorted(pol, reverse=True)


def test_football_present_for_a_football_follower():
    for preset in ("short", "medium", "detailed"):
        assert "sport" in _tops(_cap(preset)), preset


def test_depth_cap_and_ceiling():
    for preset in ("short", "medium", "detailed"):
        rows = _cap(preset)
        assert len(rows) <= MAX_BULLETIN_STORIES
        per_cat: dict[str, int] = {}
        for t in _tops(rows):
            per_cat[t] = per_cat.get(t, 0) + 1
        assert max(per_cat.values()) <= PRESET_DEPTH[preset]


def test_presets_give_distinct_sizes():
    sizes = [len(_cap(p)) for p in ("short", "medium", "detailed")]
    assert sizes[0] < sizes[1] < sizes[2], sizes     # Quick < Standard < Deep


def test_thin_category_absent_not_padded():
    # Add science to the selection but provide no science that clears the bar → absent.
    sel = SEL + ["science"]
    rows = qualify_and_order(_pool(), include_top_stories=True, include_categories=sel, preset="detailed", now=NOW)
    assert "science" not in _tops(rows)


def test_fresh_relief_clears_a_just_under_bar_breaking_story():
    # 6.7 is under the Quick bar (7.0). Old → excluded; genuinely fresh → relief clears it.
    old = _pool() + [_ss("brk_old", "politics.uk", 6.7, hours_ago=10.0)]
    fresh = _pool() + [_ss("brk_new", "politics.uk", 6.7, hours_ago=0.05)]
    q_old = {r.candidate.story_id for r in qualify_and_order(old, include_top_stories=True, include_categories=SEL, preset="short", now=NOW)}
    q_new = {r.candidate.story_id for r in qualify_and_order(fresh, include_top_stories=True, include_categories=SEL, preset="short", now=NOW)}
    assert "brk_old" not in q_old
    assert "brk_new" in q_new
