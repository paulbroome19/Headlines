"""
Bridge/transition binding guard.

A transition (bridge) is written by the connective LLM for one specific ordered PAIR of stories:
it follows `prev_story_id` and previews `next_story_id`. In the assembled segment list it is
placed positionally, between those two story segments. But playback can reorder or shrink the
running order AFTER the bridge text is written — a user skips a story, or the streaming skeleton
reconciles to the deduped final order (#161/#162). A positional transition then ends up between
the WRONG two stories and names a story that already played ("now onto Argentina" after Belgium,
two stories late) — the single worst thing a bulletin can say to a new user.

This validates that each transition still sits between EXACTLY the pair it was written for. A
transition that doesn't is STALE: the caller must not play it as written — drop it to a clean cut
(silence between the stories is fine) or regenerate that one bridge for the real pair. iOS mirrors
this function at play time on the live (possibly reordered) segment list.
"""
from __future__ import annotations


def _adjacent_story_id(segments: list[dict], i: int, step: int) -> str | None:
    """The story_id of the nearest STORY segment before (step=-1) or after (step=+1) index i,
    skipping over intervening non-story segments (other transitions, etc.)."""
    j = i + step
    while 0 <= j < len(segments):
        if segments[j].get("type") == "story":
            sid = segments[j].get("story_id")
            return str(sid) if sid is not None else None
        j += step
    return None


def is_transition_stale(segments: list[dict], i: int) -> bool:
    """True if the transition at index i names a pair that no longer matches its neighbours.
    An unbound transition (no prev/next ids — e.g. an older bulletin) can't be validated and is
    treated as NOT stale (nothing better to do; the binding is what makes the guard possible)."""
    seg = segments[i]
    if seg.get("type") != "transition":
        return False
    want_prev = seg.get("prev_story_id")
    want_next = seg.get("next_story_id")
    if want_prev is None and want_next is None:
        return False  # unbound — cannot validate
    return (_adjacent_story_id(segments, i, -1) != (None if want_prev is None else str(want_prev))
            or _adjacent_story_id(segments, i, +1) != (None if want_next is None else str(want_next)))


def stale_transition_indices(segments: list[dict]) -> list[int]:
    """Indices of every transition whose written pair no longer matches its neighbours in `segments`
    — the transitions that must be dropped/regenerated rather than played."""
    return [i for i in range(len(segments)) if is_transition_stale(segments, i)]
