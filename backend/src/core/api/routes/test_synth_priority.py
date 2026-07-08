"""
Skip/tap synthesis reprioritisation (forward-then-backfill).

When the user skips/taps to a not-yet-synthesised story N, `_synth_remaining` must synthesise N
NEXT, then everything after it, then backfill the earlier gap — and crucially pivot on N's BRIDGE
(the segment a tap starts on), not N's story segment, or the bridge lands in the backfill while the
client waits on it. These pin the pure reorder + unit-start logic (no Redis, no TTS).
"""
from __future__ import annotations

from core.api.routes.data import (
    _reprioritise_order,
    _start_pack_boundary,
    _unit_start_index,
    _SAFE_START_STORIES,
)


def _segments():
    # intro, lead(opener+remainder, same story_id "s1"), then (transition, story) for s2..s5, outro.
    return [
        {"type": "intro"},                                         # 0
        {"type": "story", "story_id": "s1", "lead_part": "opener"},    # 1
        {"type": "story", "story_id": "s1", "lead_part": "remainder"}, # 2
        {"type": "transition"},                                    # 3
        {"type": "story", "story_id": "s2"},                       # 4
        {"type": "transition"},                                    # 5
        {"type": "story", "story_id": "s3"},                       # 6
        {"type": "transition"},                                    # 7
        {"type": "story", "story_id": "s4"},                       # 8
        {"type": "transition"},                                    # 9
        {"type": "story", "story_id": "s5"},                       # 10
        {"type": "outro"},                                         # 11
    ]


def test_unit_start_index_pivots_on_the_bridge_not_the_story():
    idx = _unit_start_index(_segments())
    # The lead has no preceding transition → its own story index; every other story → its bridge.
    assert idx == {"s1": 1, "s2": 3, "s3": 5, "s4": 7, "s5": 9}


def test_start_pack_boundary_covers_intro_and_first_two_stories():
    # _SAFE_START_STORIES=2 → intro + s1(opener+remainder) + bridge + s2 are already synthesised.
    assert _start_pack_boundary(_segments(), _SAFE_START_STORIES) == 5


def test_reprioritise_jump_puts_the_bridge_first_then_forward_then_backfill():
    remaining = [5, 6, 7, 8, 9, 10, 11]                 # everything past the start-pack boundary
    # Tap/skip to s4: pivot on its BRIDGE (index 7). Forward = bridge4,s4,bridge5,s5,outro; then the
    # skipped s3 gap (bridge3,s3) backfills last. The bridge the tap waits on is synthesised FIRST.
    out = _reprioritise_order(remaining, _unit_start_index(_segments())["s4"])
    assert out == [7, 8, 9, 10, 11, 5, 6]
    assert out[0] == 7                                  # s4's bridge, not s4's story (8), leads


def test_reprioritise_jump_to_the_immediate_next_is_noop():
    remaining = [5, 6, 7, 8, 9, 10, 11]
    # s3's bridge is already the head → reorder is a no-op (no churn).
    assert _reprioritise_order(remaining, _unit_start_index(_segments())["s3"]) == remaining


def test_reprioritise_ignores_none_and_already_synthesised():
    remaining = [7, 8, 9, 10, 11, 5, 6]
    assert _reprioritise_order(remaining, None) == remaining          # no hint
    assert _reprioritise_order(remaining, 3) == remaining             # 3 already done (not remaining)


def test_reprioritise_is_stable_after_the_bumped_unit_is_consumed():
    # After the bump, popping the head then re-applying the SAME pivot must not churn: once the
    # bridge/story leave `remaining`, the pivot is a no-op and the forward order is preserved.
    remaining = _reprioritise_order([5, 6, 7, 8, 9, 10, 11], 7)   # [7,8,9,10,11,5,6]
    remaining.pop(0)                                              # synthesised bridge4 (7)
    assert _reprioritise_order(remaining, 7) == [8, 9, 10, 11, 5, 6]  # 7 gone → stable, no reshuffle
