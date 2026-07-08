"""
Top-stories FINAL spec — threshold + filter-order selection.

The top block = the TOP_BLOCK_SIZE highest-ranked flagged stories drawn ONLY from the user's
TOGGLED regions (pure rank membership; region-grouped presentation), spare slots backfilled from
the user's followed topics, then the topic sections. Nothing outside the user's filters ever
appears. Pure — no DB/scorer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import MAX_BULLETIN_STORIES, PRESET_DEPTH, TOP_BLOCK_SIZE
from .models import ScoredStory, StoryRankingCandidate
from .thresholds import cap_bulletin, qualify_and_order

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


def _ss(sid: str, cat: str, score: float, *, hours_ago: float = 5.0,
        top: bool = False, entity: str | None = None) -> ScoredStory:
    c = StoryRankingCandidate(
        story_id=sid, title=sid, last_seen_at=NOW - timedelta(hours=hours_ago),
        article_count=3, source_count=3, primary_category=cat,
        primary_entity_id=entity, primary_entity_name=entity, primary_entity_weight=None,
        primary_topic_key=None, geo_region=None, pool_country="us", sources=(), top_story=top,
    )
    return ScoredStory(
        candidate=c, within_category_score=0.0, full_score=0.0, category_weight=1.0,
        recency_score=0.0, entity_weight=1.0, source_weight=1.0, cluster_weight=1.0,
        importance=0.0, normalized_score=score,
    )


def _final(stories, sel, preset="medium"):
    """The full composed bulletin (qualify → cap) as an ordered list of story ids."""
    q = qualify_and_order(stories, include_top_stories=True, include_categories=sel, preset=preset, now=NOW)
    return [s.candidate.story_id for s in
            cap_bulletin(q, include_top_stories=True, include_categories=sel, preset=preset)]


# ── MEMBERSHIP: top-N by rank across toggled regions ─────────────────────────

def test_membership_is_top_n_by_rank_across_toggled_regions():
    # 7 flagged (uk + us), toggle uk+us. The 5 highest-scoring make the block (pure rank, no
    # per-region cap); presented region-grouped (uk then us), within a region by rank. Ranks 6–7
    # are dropped (not followed as topics → nowhere else to go).
    stories = [
        _ss("uk9", "politics.uk", 9.0, top=True), _ss("us8", "politics.us", 8.5, top=True),
        _ss("uk7", "politics.uk", 7.5, top=True), _ss("us6", "politics.us", 6.5, top=True),
        _ss("uk5", "politics.uk", 5.5, top=True), _ss("us4", "politics.us", 4.5, top=True),
        _ss("uk3", "politics.uk", 3.5, top=True),
    ]
    order = _final(stories, ["top-stories.uk", "top-stories.us"])
    assert order == ["uk9", "uk7", "uk5", "us8", "us6"]   # top-5 by score, uk-group then us-group
    assert "us4" not in order and "uk3" not in order      # rank 6–7 dropped by the cap


def test_top_block_size_is_respected():
    stories = [_ss(f"u{i}", "politics.uk", 9.0 - i, top=True) for i in range(8)]
    order = _final(stories, ["top-stories.uk"])
    assert len(order) == TOP_BLOCK_SIZE
    assert order == [f"u{i}" for i in range(TOP_BLOCK_SIZE)]


def test_untoggled_region_never_appears():
    stories = [_ss("uk1", "politics.uk", 7.0, top=True), _ss("us1", "politics.us", 9.0, top=True)]
    assert _final(stories, ["top-stories.uk"]) == ["uk1"]   # US flagged absent despite higher score


def test_flagged_overflow_falls_to_section_if_followed():
    # 6 flagged politics.uk, toggle UK AND follow politics.uk: top 5 lead the block; the 6th is not
    # dropped — it appears in its politics section (it is in the user's filters).
    stories = [_ss(f"u{i}", "politics.uk", 9.0 - i, top=True) for i in range(6)]
    order = _final(stories, ["top-stories.uk", "politics.uk"])
    assert order[:TOP_BLOCK_SIZE] == [f"u{i}" for i in range(TOP_BLOCK_SIZE)]
    assert order[TOP_BLOCK_SIZE:] == ["u5"]


# ── PRESENTATION: region group order ─────────────────────────────────────────

def test_region_order_presentation_uk_us_europe_world():
    stories = [
        _ss("wo", "business.markets", 9.5, top=True),   # non-politics → 'world'
        _ss("eu", "politics.europe", 8.0, top=True),
        _ss("us", "politics.us", 7.0, top=True),
        _ss("uk", "politics.uk", 6.0, top=True),
    ]
    order = _final(stories, ["top-stories.uk", "top-stories.us", "top-stories.europe", "top-stories.world"])
    assert order == ["uk", "us", "eu", "wo"]   # config order, NOT score (world last despite top score)


# ── BACKFILL: fill spare slots from followed topics ──────────────────────────

def test_backfill_fills_spare_slots_from_followed_topics_in_topic_order():
    stories = [
        _ss("uk1", "politics.uk", 9.0, top=True), _ss("uk2", "politics.uk", 8.0, top=True),
        _ss("bz1", "business.markets", 7.5), _ss("bz2", "business.markets", 7.0),
        _ss("tc1", "technology.ai", 6.9), _ss("tc2", "technology.ai", 6.5),
    ]
    sel = ["top-stories.uk", "business.markets", "technology.ai"]
    order = _final(stories, sel)
    # 2 flagged + 3 backfill (highest-ranked topic: bz1,bz2,tc1), backfill presented in topic order.
    assert order[:5] == ["uk1", "uk2", "bz1", "bz2", "tc1"]
    # backfilled stories are consumed — only tc2 is left for the technology section.
    assert order[5:] == ["tc2"]
    assert order.count("bz1") == 1 and order.count("tc1") == 1


def test_backfill_draws_only_from_followed_topics():
    stories = [
        _ss("uk1", "politics.uk", 9.0, top=True),
        _ss("bz1", "business.markets", 8.0),      # followed
        _ss("cu1", "culture.tv-film", 9.9),       # NOT followed → never appears, even to backfill
    ]
    order = _final(stories, ["top-stories.uk", "business.markets"])
    assert "cu1" not in order
    assert order[:2] == ["uk1", "bz1"]


def test_no_backfill_when_block_is_full():
    stories = [_ss(f"u{i}", "politics.uk", 9.0 - i, top=True) for i in range(5)] + \
              [_ss("bz1", "business.markets", 8.5)]
    order = _final(stories, ["top-stories.uk", "business.markets"])
    assert order[:TOP_BLOCK_SIZE] == [f"u{i}" for i in range(TOP_BLOCK_SIZE)]
    assert order[TOP_BLOCK_SIZE:] == ["bz1"]   # bz1 stays in its section, not pulled up


# ── OFF-STATE ────────────────────────────────────────────────────────────────

def test_off_state_no_block_sections_by_topic_order():
    # No top-stories.<region> toggled → Top Stories OFF: no block. A flagged story appears only as
    # an ordinary topic story (if followed); sections run in filter order, within-bucket by rank.
    stories = [_ss("uk1", "politics.uk", 9.0, top=True),
               _ss("po1", "politics.uk", 7.0),
               _ss("bz1", "business.markets", 8.0)]
    order = _final(stories, ["politics.uk", "business.markets"])
    assert order == ["uk1", "po1", "bz1"]   # politics section (by rank) then business — no top block


# ── Region-group ordering (carried from #169) ────────────────────────────────

def test_top_block_is_strict_region_order_uk_first():
    order = _final([
        _ss("uk_ord", "politics.uk", 7.0, top=True), _ss("us_ord", "politics.us", 7.5, top=True),
        _ss("us_huge", "politics.us", 9.0, top=True), _ss("uk_big", "politics.uk", 8.0, top=True),
    ], ["top-stories"])
    assert order == ["uk_big", "uk_ord", "us_huge", "us_ord"]   # all UK (by score) then all US


def test_todays_prod_case_uk_leads_over_higher_scoring_us():
    order = _final([
        _ss("nato", "politics.us", 9.0, top=True), _ss("sanders", "politics.us", 8.4, top=True),
        _ss("farage", "politics.uk", 8.2, top=True), _ss("harry", "politics.uk", 8.1, top=True),
    ], ["top-stories"])
    assert order[:2] == ["farage", "harry"] and order[2:] == ["nato", "sanders"]


# ── Topic-section invariants (unchanged behaviour) ───────────────────────────

def _tops(order, stories):
    by = {s.candidate.story_id: (s.candidate.primary_category or "").split(".")[0] for s in stories}
    return [by[sid] for sid in order]


def test_sport_is_last_and_football_present_for_a_follower():
    stories = [_ss("uk1", "politics.uk", 9.0, top=True),
               _ss("bz1", "business.markets", 8.0),
               _ss("fb1", "sport.football.premier-league", 8.5)]
    sel = ["top-stories.uk", "business.markets", "sport.football.premier-league"]
    tops = _tops(_final(stories, sel), stories)
    assert "sport" in tops
    assert tops.index("sport") == len(tops) - 1   # sport last


def test_depth_cap_and_ceiling():
    # A FULL block (5 flagged) → no backfill, so business appears only in its SECTION, capped at
    # PRESET_DEPTH. (When the block backfills, promoted topic stories sit in top position, above
    # and beyond the section depth — that is by design.)
    stories = [_ss(f"u{i}", "politics.uk", 9.0 - i * 0.01, top=True) for i in range(5)] + \
              [_ss(f"b{i}", "business.markets", 8.0 - i * 0.1) for i in range(12)]
    sel = ["top-stories.uk", "business.markets"]
    for preset in ("short", "medium", "detailed"):
        order = _final(stories, sel, preset)
        assert len(order) <= MAX_BULLETIN_STORIES
        biz = [sid for sid in order if sid.startswith("b")]
        assert len(biz) <= PRESET_DEPTH[preset]   # depth cap on the topic section
