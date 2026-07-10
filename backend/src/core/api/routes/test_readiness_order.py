"""E: the readiness `story_order` — the authoritative running order the client reconciles TO.

Pure function; no DB. Dedups a split lead (two consecutive story segments, one story_id) and
preserves play order.
"""
from core.api.routes.data import authoritative_story_order


def test_dedups_split_lead_and_preserves_order():
    segs = [
        {"index": 0, "type": "intro"},
        {"index": 1, "type": "transition", "prev_story_id": None, "next_story_id": "A"},
        {"index": 2, "type": "story", "story_id": "A"},   # opener
        {"index": 3, "type": "story", "story_id": "A"},   # remainder (split lead — same id)
        {"index": 4, "type": "transition", "prev_story_id": "A", "next_story_id": "B"},
        {"index": 5, "type": "story", "story_id": "B"},
        {"index": 6, "type": "outro"},
    ]
    assert authoritative_story_order(segs) == ["A", "B"]


def test_ignores_missing_ids_and_non_story():
    segs = [
        {"type": "intro"},
        {"type": "story"},                    # no story_id → skipped
        {"type": "story", "story_id": "X"},
        {"type": "transition"},
        {"type": "story", "story_id": "Y"},
    ]
    assert authoritative_story_order(segs) == ["X", "Y"]


def test_empty():
    assert authoritative_story_order([]) == []
