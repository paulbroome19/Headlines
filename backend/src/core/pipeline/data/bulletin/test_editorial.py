"""Unit tests for the editorial pass: bounded application (significance clamp, category
guard, promotion cap) and the review parser (mocked Anthropic call)."""
from __future__ import annotations

from datetime import datetime, timezone

from core.pipeline.ranking.models import ScoredStory, StoryRankingCandidate
from core.pipeline.data.bulletin import editorial as E
from core.pipeline.data.bulletin.editorial import EditorialAdjustment, MAX_PROMOTIONS, SIG_CLAMP
from core.pipeline.data.bulletin.editorial_review import (
    apply_editorial, _tiered_review, TIER_DEPTH, FLOOR_N,
)

NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
LEAVES = ["politics.uk", "politics.us", "business.companies", "technology.gaming",
          "culture.tv-film", "top-stories.uk", "top-stories.europe", "top-stories.asia", "science"]


def _story(sid: str, cat: str | None, score: float, region: str | None = None) -> ScoredStory:
    c = StoryRankingCandidate(
        story_id=sid, title=sid, last_seen_at=NOW, article_count=5, source_count=5,
        primary_category=cat, primary_entity_id=None, primary_entity_name=None,
        primary_entity_weight=None, primary_topic_key=None, geo_region=region,
    )
    return ScoredStory(candidate=c, within_category_score=0.0, full_score=0.0, category_weight=1.0,
                       recency_score=0.0, entity_weight=0.0, source_weight=0.0, cluster_weight=0.0,
                       normalized_score=score)


# ── apply_editorial: bounded, in place ───────────────────────────────────────

def test_significance_delta_applied_and_score_clamped_to_0_10():
    s = _story("a", "technology.gaming", 9.5)
    apply_editorial([s], {"a": EditorialAdjustment("a", None, +2.0, "up")})
    assert s.normalized_score == 10.0            # 9.5+2 clamped to 10
    d = _story("b", "science", 1.0)
    apply_editorial([d], {"b": EditorialAdjustment("b", None, -2.0, "down")})
    assert d.normalized_score == 0.0             # 1-2 clamped to 0


def test_category_correction_applied():
    s = _story("a", "politics.us", 8.0)
    apply_editorial([s], {"a": EditorialAdjustment("a", "business.companies", 0.0, "biz")})
    assert s.candidate.primary_category == "business.companies"


def test_top_story_flag_is_separate_from_category():
    # A category correction is applied AND the top_story flag is set — independently. Category
    # stays the real subject (never rewritten to top-stories.*).
    s = _story("a", "technology.gaming", 8.0)
    apply_editorial([s], {"a": EditorialAdjustment("a", "politics.us", 0.0, "war", top_story=True)})
    assert s.candidate.primary_category == "politics.us"   # corrected subject
    assert s.candidate.top_story is True                    # flagged on top


def test_top_story_cap_keeps_strongest():
    # N+2 stories all flagged top_story; only the strongest MAX_PROMOTIONS take the flag.
    n = MAX_PROMOTIONS + 2
    stories = [_story(f"s{i}", "science", 5.0 + i * 0.1) for i in range(n)]
    adj = {f"s{i}": EditorialAdjustment(f"s{i}", None, 0.0, "big", top_story=True) for i in range(n)}
    apply_editorial(stories, adj)
    flagged = [s for s in stories if s.candidate.top_story]
    assert len(flagged) == MAX_PROMOTIONS
    assert stories[0].candidate.top_story is False    # weakest two not flagged
    assert stories[-1].candidate.top_story is True    # strongest flagged
    # category is never touched by the flag
    assert all(s.candidate.primary_category == "science" for s in stories)


def test_unknown_story_id_ignored():
    s = _story("a", "science", 5.0)
    changed = apply_editorial([s], {"ghost": EditorialAdjustment("ghost", "politics.uk", 1.0, "x")})
    assert changed == 0 and s.candidate.primary_category == "science"


# ── tier-laddered review selection ───────────────────────────────────────────

def test_tiered_depth_by_category_plus_floor_and_bands():
    # 60 politics (T1, cap 50), 30 science (T2, cap 20), 8 arsenal (T3 leaf, cap 5).
    pol = [_story(f"p{i}", "politics.uk", 100 - i) for i in range(60)]
    sci = [_story(f"s{i}", "science", 60 - i) for i in range(30)]
    ars = [_story(f"a{i}", "sport.football.premier-league.arsenal", 40 - i) for i in range(8)]
    review, rank = _tiered_review(pol + sci + ars)
    ids = {s.candidate.story_id for s, _ in review}
    bands = {s.candidate.story_id: b for s, b in review}

    # politics: top 50 by tier depth — but the FLOOR keeps each leaf's top-2 too; politics
    # is one leaf here so depth 50 dominates. 60 present, 50 within depth + none extra.
    assert sum(1 for i in ids if i.startswith("p")) == TIER_DEPTH[1]
    assert sum(1 for i in ids if i.startswith("s")) == TIER_DEPTH[2]        # science top 20
    assert sum(1 for i in ids if i.startswith("a")) == 5                    # niche leaf top 5
    # tier-1 politics → band A (front-page critical); science + sport → band B
    assert bands["p0"] == "A" and bands["s0"] == "B" and bands["a0"] == "B"
    # global rank is by score across everything (politics highest)
    assert rank["p0"] == 1


def test_floor_includes_a_leaf_below_its_tier_depth():
    # A niche leaf with only 1 story ranked far down still gets reviewed (floor top-2).
    big = [_story(f"b{i}", "politics.uk", 100 - i) for i in range(50)]
    niche = [_story("n1", "sport.tennis", 1.0)]   # rank ~51, tier 3 leaf
    review, _ = _tiered_review(big + niche)
    assert "n1" in {s.candidate.story_id for s, _ in review}


# ── review_front_page: parsing + guards (mocked API) ─────────────────────────

def _meta(sid, cat="science"):
    return E.StoryMeta(story_id=sid, headline=sid, snippet="", category=cat, sources=5, rank=1)


def _mock(monkeypatch, text_payload: str):
    monkeypatch.setattr(E, "_post_anthropic",
                        lambda system, user: {"content": [{"text": text_payload}]})


def test_review_parses_and_guards(monkeypatch):
    _mock(monkeypatch, '{"adjustments":['
          '{"id":"1","category":"business.companies","significance":-1,"reason":"biz"},'
          '{"id":"2","category":"climate","significance":0,"reason":"bad slug"},'   # invalid cat, sig 0 → skipped
          '{"id":"3","category":"top-stories.europe","significance":5,"reason":"major"},'  # sig clamped
          '{"id":"99","significance":2,"reason":"unknown id"}]}')                    # unknown → ignored
    adj = E.review_front_page([_meta("1"), _meta("2"), _meta("3")], LEAVES)
    assert set(adj) == {"1", "3"}
    assert adj["1"].category == "business.companies" and adj["1"].significance_delta == -1
    assert adj["3"].category == "top-stories.europe"
    assert adj["3"].significance_delta == SIG_CLAMP   # 5 clamped to +2


def test_review_empty_on_api_failure(monkeypatch):
    monkeypatch.setattr(E, "_post_anthropic", lambda system, user: None)
    assert E.review_front_page([_meta("1")], LEAVES) == {}


def test_review_tolerates_trailing_prose(monkeypatch):
    _mock(monkeypatch, '{"adjustments":[{"id":"1","significance":-1,"reason":"x"}]}\n\nDone.')
    assert E.review_front_page([_meta("1")], LEAVES)["1"].significance_delta == -1


# ── region-relative UK bar (stopgap) ─────────────────────────────────────────

def test_rubric_is_region_relative_and_illustrative():
    # The bar for a UK story is region-relative ("would lead the UK national bulletin"), the UK
    # examples are ILLUSTRATIVE not an exhaustive checklist, and the other exclusions are unchanged.
    sys = E.build_system(LEAVES)
    assert "lead the UK national bulletin" in sys          # region-relative framing
    assert "compete for one slot" in sys                   # non-competitive with the biggest global story
    assert "For example" in sys and "ILLUSTRATIONS of the principle" in sys  # illustrative, not a list
    # strictness elsewhere is untouched
    assert "Corporate/business news" in sys and "Tech/product news" in sys


def test_uk_flag_survives_alongside_bigger_us_story():
    # A significant-for-UK story (lower coverage/score) flagged alongside a BIGGER US story: both
    # keep the flag. A UK top story is never dropped for being smaller than a US one. (The
    # region-relative JUDGMENT lives in the prompt — guarded above; this guards the apply path.)
    uk = _story("uk1", "politics.uk", 6.0)
    us = _story("us1", "politics.us", 9.0)
    apply_editorial([uk, us], {
        "uk1": EditorialAdjustment("uk1", None, +1.0, "major UK court ruling", top_story=True),
        "us1": EditorialAdjustment("us1", None, 0.0, "major US story", top_story=True),
    })
    assert uk.candidate.top_story is True and us.candidate.top_story is True


def test_front_page_window_widened_to_100():
    from core.pipeline.data.bulletin.editorial_review import FRONT_PAGE_N
    assert FRONT_PAGE_N == 100   # was 70 — pulls high-merit/low-coverage stories into review


def test_rubric_version_invalidates_fingerprint(monkeypatch):
    # A bar-version bump must change the fingerprint so the same candidate set is RE-reviewed
    # under the new rubric instead of copying a pre-change twin's verdicts.
    from core.pipeline.data.bulletin import editorial_review as ER
    stories = [_story("a", "science", 5.0), _story("b", "science", 4.0)]
    fp1 = ER._candidate_fingerprint(stories)
    monkeypatch.setattr(ER, "RUBRIC_VERSION", ER.RUBRIC_VERSION + 1)
    assert ER._candidate_fingerprint(stories) != fp1
