"""Unit tests for the LLM-primary categoriser: the valid-slug guard/reroute, the
tolerant JSON parser, and classify_story end-to-end with a mocked Anthropic call."""
from __future__ import annotations

from . import llm_categoriser as L
from .categorisation_repo import content_hash

LEAVES = [
    "politics.uk", "politics.us", "politics.world",
    "business.markets", "business.companies", "health", "science",
    "sport.football.premier-league.arsenal", "sport.cricket.international",
    "technology.gaming", "technology.companies", "top-stories.uk",
]
VSET = set(LEAVES)
NODES = L._ancestor_nodes(LEAVES)


# ── valid-slug guard / reroute ───────────────────────────────────────────────

def test_valid_leaf_passes_through():
    assert L.guard_and_reroute("politics.uk", VSET, NODES) == "politics.uk"


def test_taxonomy_gap_reroutes_to_nearest_ancestor():
    # invented deeper slugs snap up to the nearest node that exists
    assert L.guard_and_reroute("sport.nhl", VSET, NODES) == "sport"
    assert L.guard_and_reroute("top-stories.north-america", VSET, NODES) == "top-stories"
    assert L.guard_and_reroute("sport.cricket.internationals", VSET, NODES) == "sport.cricket"


def test_legacy_and_invented_top_levels_are_rejected():
    assert L.guard_and_reroute("world.europe", VSET, NODES) is None
    assert L.guard_and_reroute("entertainment.celebrity", VSET, NODES) is None
    assert L.guard_and_reroute("climate", VSET, NODES) is None
    assert L.guard_and_reroute("made.up.thing", VSET, NODES) is None


# ── tolerant JSON parser ─────────────────────────────────────────────────────

def _raw(text: str) -> dict:
    return {"content": [{"text": text}]}


def test_parse_clean_json():
    got = L._parse(_raw('{"primary":"health","secondary":[],"confidence":0.9,"reason":"x"}'), VSET)
    assert got[0] == "health" and got[2] == 0.9


def test_parse_tolerates_trailing_prose():
    txt = '{"primary":"health","secondary":[],"confidence":0.9,"reason":"x"}\n\nThis is health news.'
    assert L._parse(_raw(txt), VSET)[0] == "health"


def test_parse_strips_code_fence():
    txt = '```json\n{"primary":"science","secondary":[],"confidence":0.8,"reason":"y"}\n```'
    assert L._parse(_raw(txt), VSET)[0] == "science"


# ── classify_story with a mocked API ─────────────────────────────────────────

def _mock_post(monkeypatch, payload_text: str):
    monkeypatch.setattr(L, "_post_anthropic", lambda system, user: _raw(payload_text))


def test_classify_returns_valid_leaf(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"politics.uk","secondary":["top-stories.uk"],"confidence":0.95,"reason":"uk gov"}')
    r = L.classify_story(title="No 10 shares shock at smuggler", snippets=[],
                         pool_topic="nation", pool_country="gb", valid_leaves=LEAVES)
    assert r.primary == "politics.uk"
    assert r.secondaries == ["top-stories.uk"]
    assert r.method == "llm-primary"


def test_classify_reroutes_taxonomy_gap(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"sport.nhl","secondary":[],"confidence":0.9,"reason":"hockey"}')
    r = L.classify_story(title="Oilers move on", snippets=[], pool_topic="sports",
                         pool_country="us", valid_leaves=LEAVES)
    assert r.primary == "sport" and r.method == "llm-primary-rerouted"
    assert r.raw_slug == "sport.nhl"


def test_classify_rejects_unrerouteable(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"climate","secondary":[],"confidence":0.9,"reason":"legacy"}')
    assert L.classify_story(title="Heatwave", snippets=[], pool_topic=None,
                            pool_country=None, valid_leaves=LEAVES) is None


def test_classify_filters_invalid_secondaries(monkeypatch):
    _mock_post(monkeypatch, '{"primary":"health","secondary":["climate","science","health"],"confidence":0.9,"reason":"z"}')
    r = L.classify_story(title="Cancer study", snippets=[], pool_topic=None,
                         pool_country=None, valid_leaves=LEAVES)
    assert r.secondaries == ["science"]   # climate dropped (invalid), health dropped (== primary)


def test_classify_none_on_api_failure(monkeypatch):
    monkeypatch.setattr(L, "_post_anthropic", lambda system, user: None)
    assert L.classify_story(title="x", snippets=[], pool_topic=None,
                            pool_country=None, valid_leaves=LEAVES) is None


# ── content hash ─────────────────────────────────────────────────────────────

def test_content_hash_stable_and_sensitive():
    a = content_hash(title="T", snippets=["s"], pool_topic="business", pool_country="gb", taxonomy_version=2)
    b = content_hash(title="T", snippets=["s"], pool_topic="business", pool_country="gb", taxonomy_version=2)
    c = content_hash(title="T", snippets=["s"], pool_topic="business", pool_country="gb", taxonomy_version=3)
    assert a == b and a != c
