"""Unit tests for the LLM-primary categoriser: the valid-slug guard/reroute, the
tolerant JSON parser, and classify_story end-to-end with a mocked Anthropic call."""
from __future__ import annotations

from . import llm_categoriser as L
from .categorisation_repo import content_hash

LEAVES = [
    "politics.uk", "politics.us", "politics.world",
    "business.markets", "business.companies", "health", "science",
    "sport.football.premier-league.arsenal", "sport.football.internationals",
    "sport.cricket.internationals", "sport.nba", "sport.nfl",
    "technology.gaming", "technology.companies", "top-stories.uk",
]
OFFERED = L.offered_targets(LEAVES)   # leaves + top-level buckets


# ── valid-slug guard: keep / snap-to-leaf / drop (never reroute to a coarse ancestor) ──

def test_offered_targets_are_leaves_plus_top_levels():
    assert "politics.uk" in OFFERED            # a leaf
    assert "business" in OFFERED and "sport" in OFFERED  # top-level buckets
    assert "sport.football" not in OFFERED      # a bare intermediate is NOT an offer


def test_valid_leaf_and_offered_parent_are_kept():
    assert L.guard_valid("politics.uk", LEAVES) == "politics.uk"
    assert L.guard_valid("business", LEAVES) == "business"   # genuine general bucket


def test_near_miss_snaps_to_a_real_leaf():
    # plural/singular drift → snap to the canonical leaf
    assert L.guard_valid("sport.cricket.international", LEAVES) == "sport.cricket.internationals"
    assert L.guard_valid("sport.football.international", LEAVES) == "sport.football.internationals"
    # spurious intermediate segment → snap to the real leaf
    assert L.guard_valid("sport.basketball.nba", LEAVES) == "sport.nba"
    # spurious child on a leaf that has none → snap to the leaf
    assert L.guard_valid("health.pediatrics", LEAVES) == "health"


def test_single_league_sport_alias_snaps_to_its_leaf():
    # basketball / American football exist only as one league → generic parent snaps, not drops
    assert L.guard_valid("sport.basketball", LEAVES) == "sport.nba"
    assert L.guard_valid("basketball", LEAVES) == "sport.nba"
    assert L.guard_valid("sport.american-football", LEAVES) == "sport.nfl"


def test_offtaxonomy_subject_is_DROPPED_never_snapped_to_a_coarse_ancestor():
    # no near-miss resolves to a real leaf → drop; NEVER snap up to a coarse bucket
    assert L.guard_valid("sport.nhl", LEAVES) is None          # ice hockey — no leaf; not → 'sport'
    assert L.guard_valid("top-stories.north-america", LEAVES) is None  # unlisted region
    assert L.guard_valid("technology.robotics", LEAVES) is None  # not a leaf; not → 'technology'


def test_none_sentinel_and_legacy_slugs_dropped():
    assert L.guard_valid("none", LEAVES) is None
    assert L.guard_valid("world.europe", LEAVES) is None
    assert L.guard_valid("entertainment.celebrity", LEAVES) is None
    assert L.guard_valid("climate", LEAVES) is None


# ── tolerant JSON parser ─────────────────────────────────────────────────────

def _raw(text: str) -> dict:
    return {"content": [{"text": text}]}


def test_parse_clean_json():
    got = L._parse(_raw('{"primary":"health","secondary":[],"confidence":0.9,"reason":"x"}'), OFFERED)
    assert got[0] == "health" and got[2] == 0.9


def test_parse_tolerates_trailing_prose():
    txt = '{"primary":"health","secondary":[],"confidence":0.9,"reason":"x"}\n\nThis is health news.'
    assert L._parse(_raw(txt), OFFERED)[0] == "health"


def test_parse_strips_code_fence():
    txt = '```json\n{"primary":"science","secondary":[],"confidence":0.8,"reason":"y"}\n```'
    assert L._parse(_raw(txt), OFFERED)[0] == "science"


# ── classify_story with a mocked API: KEEP vs DROP vs FAILURE ─────────────────

def _mock_post(monkeypatch, payload_text: str):
    monkeypatch.setattr(L, "_post_anthropic", lambda system, user: _raw(payload_text))


def test_classify_keeps_valid_leaf(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"politics.uk","secondary":["top-stories.uk"],"confidence":0.95,"reason":"uk gov"}')
    r = L.classify_story(title="No 10 shares shock at smuggler", snippets=[],
                         pool_topic="nation", pool_country="gb", valid_leaves=LEAVES)
    assert r.primary == "politics.uk" and r.method == "llm-primary"
    assert r.secondaries == ["top-stories.uk"]


def test_classify_keeps_offered_parent(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"business","secondary":[],"confidence":0.7,"reason":"general business"}')
    r = L.classify_story(title="Markets roundup", snippets=[], pool_topic="business",
                         pool_country="gb", valid_leaves=LEAVES)
    assert r.primary == "business"


def test_classify_DROPS_offtaxonomy_subject(monkeypatch):
    # ice hockey: model may (wrongly) emit a hockey slug OR correctly say none — either way DROP.
    _mock_post(monkeypatch, '{"primary":"sport.nhl","secondary":[],"confidence":0.9,"reason":"hockey"}')
    r = L.classify_story(title="Oilers move on", snippets=[], pool_topic="sports",
                         pool_country="us", valid_leaves=LEAVES)
    assert r.primary == L.DROP and r.method == "llm-drop"
    assert r.reason == "hockey"          # model's reason preserved for visibility


def test_classify_DROPS_on_explicit_none(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"none","secondary":[],"confidence":0.4,"reason":"no fit"}')
    r = L.classify_story(title="Local curling bonspiel result", snippets=[], pool_topic=None,
                         pool_country=None, valid_leaves=LEAVES)
    assert r.primary == L.DROP


def test_classify_DROPS_legacy_slug(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"climate","secondary":[],"confidence":0.9,"reason":"legacy"}')
    assert L.classify_story(title="Heatwave", snippets=[], pool_topic=None,
                            pool_country=None, valid_leaves=LEAVES).primary == L.DROP


def test_classify_filters_invalid_secondaries(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"health","secondary":["climate","science","health"],"confidence":0.9,"reason":"z"}')
    r = L.classify_story(title="Cancer study", snippets=[], pool_topic=None,
                         pool_country=None, valid_leaves=LEAVES)
    assert r.secondaries == ["science"]   # climate dropped (invalid), health dropped (== primary)


def test_classify_none_on_api_failure_is_distinct_from_drop(monkeypatch):
    monkeypatch.setattr(L, "_post_anthropic", lambda system, user: None)
    r = L.classify_story(title="x", snippets=[], pool_topic=None,
                         pool_country=None, valid_leaves=LEAVES)
    assert r is None   # API failure → None (fallback), NOT DROPPED (exclude)


# ── content hash ─────────────────────────────────────────────────────────────

def test_content_hash_stable_and_sensitive():
    a = content_hash(title="T", snippets=["s"], pool_topic="business", pool_country="gb", taxonomy_version=2)
    b = content_hash(title="T", snippets=["s"], pool_topic="business", pool_country="gb", taxonomy_version=2)
    c = content_hash(title="T", snippets=["s"], pool_topic="business", pool_country="gb", taxonomy_version=3)
    assert a == b and a != c
