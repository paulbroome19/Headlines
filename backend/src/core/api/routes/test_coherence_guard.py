"""
Headline↔standfirst coherence guard (audit risk #3).

The home preview headline is the LIVE representative article; the standfirst is the latest cached
summary. If the summary was built from different articles than the story's current top-5 (its
content_hash differs), the standfirst may describe a DIFFERENT event — so it is suppressed.
"""
from __future__ import annotations

from .data import _coherent_standfirst


def test_matching_hash_keeps_standfirst():
    txt = "The government is bringing in new rules on renting that limit no-fault evictions."
    out = _coherent_standfirst(txt, "abc123", "abc123")
    assert out and "renting" in out


def test_mismatched_hash_suppresses_standfirst():
    # Summary was written for old content (hash 'old'); the story's current top-5 hash 'new'
    # → the standfirst may be about a different event → suppress.
    assert _coherent_standfirst("Text about a since-displaced event.", "old", "new") is None


def test_missing_hash_keeps_standfirst_backcompat():
    # Rows without a content_hash (or no current articles) can't be compared → keep (no regression).
    assert _coherent_standfirst("Some summary.", None, "new") is not None
    assert _coherent_standfirst("Some summary.", "old", None) is not None
