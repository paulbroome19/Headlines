"""
Daily-edition reconcile: the target is a length TARGET refilled from a deep reservoir,
not a hard slice of the top-N that erodes as the user hears/skips stories.

The bug these pin: capping to the target BEFORE dropping consumed/rejected stories
(with no refill) meant every heard story permanently shortened the bulletin. The fix
sizes the caller's `fresh_ids` as a reservoir (> target) and caps AFTER dropping, so
freed slots refill from deeper candidates.
"""
from __future__ import annotations

from core.pipeline.data.bulletin.edition import reconcile_edition


def test_refills_to_target_from_deep_reservoir():
    # 6 unheard already in the edition; 4 of them heard since. A reservoir of 20 fresh
    # candidates must refill the freed slots back up to the target (10), not leave 2.
    existing = [f"e{i}" for i in range(6)]          # e0..e5
    heard = {"e0", "e1", "e2", "e3"}                # 4 heard -> 2 unheard kept
    fresh = existing + [f"f{i}" for i in range(20)]  # deep reservoir
    out = reconcile_edition(existing, fresh, heard, max_size=10)
    assert len(out) == 10                            # refilled to target, not 2
    assert not (set(out) & heard)                    # heard never reappears
    assert out[:2] == ["e4", "e5"]                   # unheard kept, in order


def test_heard_stories_never_reappear_even_if_top_ranked():
    existing = ["a", "b"]
    heard = {"a"}
    fresh = ["a", "c", "d", "e"]                     # 'a' is #1 fresh but is heard
    out = reconcile_edition(existing, fresh, heard, max_size=3)
    assert "a" not in out
    assert len(out) == 3
    assert out[0] == "b"                             # kept unheard leads (a is heard)


def test_breaking_lead_spliced_to_front():
    existing = ["b", "c"]
    heard: set[str] = set()
    fresh = ["NEW", "b", "c", "d"]                   # a genuinely bigger new #1
    out = reconcile_edition(existing, fresh, heard, max_size=4)
    assert out[0] == "NEW"                           # spliced to the front
    assert out[1:3] == ["b", "c"]                    # existing unheard preserved after


def test_genuine_exhaustion_still_shortens():
    # Only 3 unheard candidates exist anywhere -> honestly returns 3, not padded.
    existing = ["a", "b"]
    heard = {"a"}
    fresh = ["a", "b", "c"]                          # after dropping 'a': b, c + kept b
    out = reconcile_edition(existing, fresh, heard, max_size=10)
    assert set(out) == {"b", "c"}
    assert len(out) == 2                             # < target, but that's real scarcity


def test_no_duplicates():
    existing = ["a", "b", "c"]
    fresh = ["a", "b", "c", "d", "e"]
    out = reconcile_edition(existing, fresh, set(), max_size=10)
    assert len(out) == len(set(out))
    assert out == ["a", "b", "c", "d", "e"]
