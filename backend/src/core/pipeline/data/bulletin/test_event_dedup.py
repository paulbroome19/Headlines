"""event_dedup hardening: the LLM same-event pass retries once, then fails LOUD (never silently
fails open) — the deterministic backstop (selection.dedup_bulletin_events) then catches dupes."""
from __future__ import annotations

import logging
from types import SimpleNamespace

from core.pipeline.data.bulletin import event_dedup as ED


def _key(monkeypatch):
    monkeypatch.setattr(ED, "settings", SimpleNamespace(anthropic_api_key="k", fallback_model="haiku"))


def test_dedup_retries_once_then_returns_unchanged_and_logs_error(monkeypatch, caplog):
    calls = {"n": 0}

    def fake_post(api_key, titles):
        calls["n"] += 1
        return {"content": [{"text": "not json at all"}]}   # malformed on every attempt

    _key(monkeypatch)
    monkeypatch.setattr(ED, "_post_dedup", fake_post)
    stories = [{"story_id": "a", "headline": "Eleven dead in factory blast"},
               {"story_id": "b", "headline": "11 killed in factory explosion"}]
    with caplog.at_level(logging.ERROR):
        out = ED.dedup_same_event(stories)
    assert calls["n"] == 2                 # exactly one retry
    assert out == stories                  # fails OPEN to unchanged (backstop handles the dupe)...
    assert any(r.levelno >= logging.ERROR for r in caplog.records)   # ...but LOUDLY, never silent


def test_dedup_network_failure_retries_then_fails_loud(monkeypatch, caplog):
    calls = {"n": 0}

    def fake_post(api_key, titles):
        calls["n"] += 1
        return None                         # network/HTTP failure on every attempt

    _key(monkeypatch)
    monkeypatch.setattr(ED, "_post_dedup", fake_post)
    with caplog.at_level(logging.ERROR):
        out = ED.dedup_same_event([{"story_id": "a", "headline": "X"}, {"story_id": "b", "headline": "Y"}])
    assert calls["n"] == 2
    assert len(out) == 2
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_dedup_recovers_on_retry(monkeypatch):
    # malformed first, valid second → the retry succeeds and the dupe is collapsed.
    seq = [{"content": [{"text": "garbage"}]},
           {"content": [{"text": '{"groups": [[1, 2]]}'}]}]

    def fake_post(api_key, titles):
        return seq.pop(0)

    _key(monkeypatch)
    monkeypatch.setattr(ED, "_post_dedup", fake_post)
    out = ED.dedup_same_event([{"story_id": "a", "headline": "Eleven dead in factory blast"},
                               {"story_id": "b", "headline": "11 killed in factory explosion"}])
    assert len(out) == 1 and out[0]["story_id"] == "a"   # group [1,2] → keep best-ranked (index 0)
