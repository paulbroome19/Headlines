"""Unit tests for the editorial pass: bounded application (significance clamp, category
guard, promotion cap) and the review parser (mocked Anthropic call)."""
from __future__ import annotations

from datetime import datetime, timezone

from core.pipeline.ranking.models import ScoredStory, StoryRankingCandidate
from core.pipeline.data.bulletin import editorial as E
from core.pipeline.data.bulletin.editorial import EditorialAdjustment, MAX_PROMOTIONS, SIG_CLAMP
from core.pipeline.data.bulletin.editorial_review import apply_editorial

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


def test_promotion_cap_blocks_excess_top_story_promotions():
    # N+2 stories all promoted to top-stories; only the strongest MAX_PROMOTIONS take effect.
    n = MAX_PROMOTIONS + 2
    stories = [_story(f"s{i}", "science", 5.0 + i * 0.1) for i in range(n)]
    adj = {f"s{i}": EditorialAdjustment(f"s{i}", "top-stories.uk", 0.0, "promo") for i in range(n)}
    apply_editorial(stories, adj)
    promoted = [s for s in stories if (s.candidate.primary_category or "").startswith("top-stories")]
    assert len(promoted) == MAX_PROMOTIONS
    # the highest-score ones win (s{n-1} strongest); the two weakest stay science
    assert stories[0].candidate.primary_category == "science"
    assert stories[-1].candidate.primary_category == "top-stories.uk"


def test_unknown_story_id_ignored():
    s = _story("a", "science", 5.0)
    changed = apply_editorial([s], {"ghost": EditorialAdjustment("ghost", "politics.uk", 1.0, "x")})
    assert changed == 0 and s.candidate.primary_category == "science"


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
