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

_FP, _REGIONS, _TOPIC = _selection_context(True, ["top-stories.us", "politics.uk", "business.markets", "sport.football.premier-league"])

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)

# A football + hard-news follower: top-stories(US) + UK politics + markets + Premier League.
SEL = ["top-stories.us", "politics.uk", "business.markets", "sport.football.premier-league"]


def _ss(sid: str, cat: str, score: float, *, geo: str | None = None, hours_ago: float = 5.0,
        top: bool = False) -> ScoredStory:
    c = StoryRankingCandidate(
        story_id=sid, title=sid, last_seen_at=NOW - timedelta(hours=hours_ago),
        article_count=3, source_count=3, primary_category=cat,
        primary_entity_id=None, primary_entity_name=None, primary_entity_weight=None,
        primary_topic_key=None, geo_region=geo, pool_country="us", sources=(), top_story=top,
    )
    return ScoredStory(
        candidate=c, within_category_score=0.0, full_score=0.0, category_weight=1.0,
        recency_score=0.0, entity_weight=1.0, source_weight=1.0, cluster_weight=1.0,
        importance=0.0, normalized_score=score,
    )


def _bkt(r: ScoredStory) -> int:
    return _bucket_index(r.candidate.primary_category, r.candidate.top_story,
                         r.candidate.geo_region, _TOPIC, _FP, _REGIONS)


def _pool() -> list[ScoredStory]:
    return [
        # Genuine TOP STORIES (editorial-flagged), REAL categories + US geo → admitted via the
        # top-stories.us region into the front top-stories block.
        _ss("ts1", "politics.us", 8.0, geo="us", top=True),
        _ss("ts2", "world.middle-east", 7.5, geo="us", top=True),
        _ss("ts3", "politics.us", 6.5, geo="us", top=True),
        _ss("po1", "politics.uk", 8.6), _ss("po2", "politics.uk", 7.0), _ss("po3", "politics.uk", 6.2),
        _ss("po_lo", "politics.uk", 5.0),                         # below every bar
        _ss("bu1", "business.markets", 7.0), _ss("bu2", "business.markets", 6.5),
        _ss("fb1", "sport.football.premier-league", 8.5), _ss("fb2", "sport.football.premier-league", 7.0),
        _ss("fb3", "sport.football.premier-league", 6.1),
        _ss("cu1", "culture.tv-film", 9.0),                       # UNSELECTED soft cat, NOT a top story → never appears
        _ss("he1", "health", 7.7, geo="us", top=True),           # off-topic but a FLAGGED US top story → top-stories block
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
    buckets = [_bkt(r) for r in rows]
    assert buckets == sorted(buckets)               # non-decreasing SELECTED-source bucket
    assert buckets[0] == 0                           # top-stories block leads
    assert buckets[-1] == filter_category_index("sport")  # sport bucket last, by design


def test_only_flagged_top_stories_lead_not_high_scorers():
    # The Sony scenario: a high-scoring soft-category story that is NOT flagged top_story must
    # NOT appear via the front page/region — only editorial-flagged top stories lead.
    ids = {r.candidate.story_id for r in _cap("detailed")}
    assert "cu1" not in ids                          # culture 9.0, unselected + not flagged → absent
    # The flagged US top stories DO lead, in the top-stories block.
    top_ids = [r.candidate.story_id for r in _cap("detailed") if _bkt(r) == 0]
    assert set(top_ids) >= {"ts1", "ts2", "ts3", "he1"}


def test_region_pulls_flagged_offtopic_story_into_topstories_block():
    # A health story (not a followed topic) that is a FLAGGED big US story qualifies via the
    # top-stories.us region and sits in the front TOP-STORIES block — NOT a health section.
    rows = _cap("detailed")
    he = next(r for r in rows if r.candidate.story_id == "he1")
    assert _bkt(he) == 0                                            # top-stories block
    assert all(_bkt(r) != filter_category_index("health") for r in rows)  # no standalone health bucket


def test_unflagged_offtopic_region_story_is_excluded():
    # Same health story but NOT flagged top_story → the region no longer admits it (removing the
    # old "any story clearing the bar in this region" behaviour).
    pool = [r for r in _pool() if r.candidate.story_id != "he1"]
    pool.append(_ss("he2", "health", 7.7, geo="us", top=False))
    rows = cap_bulletin(
        qualify_and_order(pool, include_top_stories=True, include_categories=SEL, preset="detailed", now=NOW),
        include_top_stories=True, include_categories=SEL, preset="detailed")
    assert "he2" not in {r.candidate.story_id for r in rows}


def test_within_category_ordered_by_rank():
    rows = _cap("detailed")
    # Within the POLITICS topic bucket (not politics-flagged top stories, which sit in bucket 0).
    pol = [r.normalized_score for r in rows if _bkt(r) == filter_category_index("politics")]
    assert pol == sorted(pol, reverse=True)


def test_football_present_for_a_football_follower():
    for preset in ("short", "medium", "detailed"):
        assert "sport" in _tops(_cap(preset)), preset


def test_depth_cap_and_ceiling():
    for preset in ("short", "medium", "detailed"):
        rows = _cap(preset)
        assert len(rows) <= MAX_BULLETIN_STORIES
        # Depth cap applies to the SELECTED TOPIC buckets; the top-stories bucket (0) is
        # uncapped (every flagged top story is included in full).
        per_bucket: dict[int, int] = {}
        for r in rows:
            b = _bkt(r)
            if b == 0:
                continue
            per_bucket[b] = per_bucket.get(b, 0) + 1
        if per_bucket:
            assert max(per_bucket.values()) <= PRESET_DEPTH[preset]


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


# The reported scenario: a UK profile with the bare 'top-stories' front page ON. front_page=True
# admits every flagged top story regardless of geo, so these pin the top-block ORDERING.
def _top_order(stories):
    return [s.candidate.story_id for s in qualify_and_order(
        stories, include_top_stories=True, include_categories=["top-stories"], preset="medium")]


def test_top_block_is_strict_region_order_uk_first():
    # Deterministic UK-first: ALL UK-subject top stories lead the block, THEN US — regardless of
    # score. Replaces the old geo_region-weighted lean (which let a huge US story jump ahead and,
    # worse, discriminated nothing because geo_region is UK-saturated).
    order = _top_order([
        _ss("uk_ord",  "politics.uk", 7.0, top=True),
        _ss("us_ord",  "politics.us", 7.5, top=True),
        _ss("us_huge", "politics.us", 9.0, top=True),   # top score, but US → below every UK story
        _ss("uk_big",  "politics.uk", 8.0, top=True),
    ])
    assert order == ["uk_big", "uk_ord", "us_huge", "us_ord"]   # all UK (by score), then all US


def test_todays_prod_case_uk_leads_over_higher_scoring_us():
    # The live TestFlight case: NATO (politics.us) outscores the UK stories, and geo_region is
    # UK-saturated for ALL of them (source bias), yet Farage + Prince Harry (politics.uk, both
    # flagged post-#167) must LEAD the top block on a UK front page.
    order = _top_order([
        _ss("nato",    "politics.us", 9.0, geo="uk", top=True),   # geo 'uk' (source bias) — now ignored
        _ss("sanders", "politics.us", 8.4, geo="uk", top=True),
        _ss("farage",  "politics.uk", 8.2, geo="uk", top=True),
        _ss("harry",   "politics.uk", 8.1, geo="uk", top=True),
    ])
    assert order[:2] == ["farage", "harry"]        # UK subject leads
    assert order[2:] == ["nato", "sanders"]        # US after, despite higher merit


def test_region_order_config_is_respected(monkeypatch):
    # The block follows TOP_STORIES_REGION_ORDER (config-driven, per-user later): flip US ahead of
    # UK and US leads — even though the UK story scores higher. Patch the exact module
    # qualify_and_order lives in (relative vs absolute import can otherwise differ under pytest).
    import sys
    monkeypatch.setattr(sys.modules[qualify_and_order.__module__],
                        "TOP_STORIES_REGION_ORDER", ["us", "uk", "europe", "world"])
    order = _top_order([_ss("uk1", "politics.uk", 9.0, top=True),
                        _ss("us1", "politics.us", 6.0, top=True)])
    assert order == ["us1", "uk1"]


def test_single_region_day_is_pure_score_order():
    # All top stories UK-subject: region rank is equal, so ordering is by score exactly as before
    # — the region change is a no-op when the day is single-region.
    order = _top_order([_ss("a", "politics.uk", 6.0, top=True),
                        _ss("b", "politics.uk", 9.0, top=True),
                        _ss("c", "politics.uk", 7.5, top=True)])
    assert order == ["b", "c", "a"]


def test_europe_and_world_fall_after_uk_and_us():
    # Full region ladder uk → us → europe → world; a non-politics flagged top story (no subject
    # region from the prefix) sorts as 'world', last — the documented limitation.
    order = _top_order([
        _ss("world_biz", "business.markets", 9.5, top=True),   # non-politics → 'world'
        _ss("eu",        "politics.europe",  6.0, top=True),
        _ss("us",        "politics.us",      6.5, top=True),
        _ss("uk",        "politics.uk",      6.2, top=True),
    ])
    assert order == ["uk", "us", "eu", "world_biz"]   # world_biz last despite the top score
