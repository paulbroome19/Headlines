"""Bridge-binding guard: a transition must sit between the exact pair it was written for."""
from core.pipeline.data.bulletin.bridge_guard import stale_transition_indices, is_transition_stale


def _story(sid):
    return {"type": "story", "story_id": sid}


def _bridge(prev, nxt, text="x"):
    return {"type": "transition", "prev_story_id": prev, "next_story_id": nxt, "text": text}


def test_all_bridges_bound_correctly_are_not_stale():
    segs = [
        {"type": "intro"},
        _story("argentina"), _bridge("argentina", "belgium"),
        _story("belgium"),   _bridge("belgium", "third"),
        _story("third"),
        {"type": "outro"},
    ]
    assert stale_transition_indices(segs) == []


def test_argentina_belgium_case_flags_the_wrong_named_bridge():
    # The reported bug: Argentina → Belgium → third, and the bridge after Belgium says "onto
    # Argentina". Reconstructed: the transition sitting between Belgium and the third game was
    # written for the pair (argentina → third) — a stale reorder — so it names Argentina, which
    # already played. It must be flagged and NOT played as written.
    segs = [
        {"type": "intro"},
        _story("argentina"), _bridge("argentina", "belgium", "and now to Belgium"),
        _story("belgium"),   _bridge("argentina", "third", "now onto Argentina..."),  # STALE pair
        _story("third"),
        {"type": "outro"},
    ]
    stale = stale_transition_indices(segs)
    assert stale == [4]                        # only the mismatched bridge
    assert is_transition_stale(segs, 4) is True
    assert is_transition_stale(segs, 2) is False   # the correctly-bound one is fine


def test_dropped_story_makes_its_neighbour_bridge_stale():
    # A story is removed at play time (skip / dedup reconcile). The bridge that previewed it now
    # sits before the wrong next story → stale.
    segs = [
        {"type": "intro"},
        _story("a"), _bridge("a", "b"),   # b was dropped; this now sits before c
        _story("c"),
        {"type": "outro"},
    ]
    assert stale_transition_indices(segs) == [2]   # bridge a→b but actual next is c


def test_unbound_transition_is_not_validated():
    # Older bulletins without prev/next ids can't be validated — treated as not-stale (no worse
    # than today), so the guard is safe to roll out incrementally.
    segs = [{"type": "intro"}, _story("a"), {"type": "transition", "text": "..."}, _story("b")]
    assert stale_transition_indices(segs) == []


def test_intervening_non_story_segments_are_skipped():
    # The nearest STORY neighbours are what matter, not the immediate index.
    segs = [_story("a"), _bridge("a", "b"), {"type": "ad"}, _story("b")]
    assert stale_transition_indices(segs) == []
