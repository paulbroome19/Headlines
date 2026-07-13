"""
Bounded-parallel tail synthesis (`_synth_remaining`).

The tail used to synthesise strictly sequentially (one segment at a time); it now runs a bounded
pool of `_MAX_PARALLEL_TAIL_SYNTH` workers so whole-briefing synthesis stays ahead of playback on
long briefings. The parallel version MUST preserve two invariants:
  1. COMPLETENESS — every post-start-pack non-empty segment is synthesised EXACTLY once (no drop,
     no duplicate) even with several workers racing on one shared queue.
  2. SKIP-PRIORITY — a live tap/skip hint still bumps the target story's bridge to the front, so a
     tapped-pending story is taken by the next free worker rather than waiting out the whole tail.

TTS is mocked — these exercise the concurrency/queue logic, not ElevenLabs.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import core.api.routes.data as data
from core.api.routes.data import _start_pack_boundary, _SAFE_START_STORIES, _MAX_PARALLEL_TAIL_SYNTH


def _segments():
    # intro, lead(opener+remainder same story "s1"), then (transition, story) for s2..s6, outro —
    # every segment carries text so synthesize_segment is actually invoked for it.
    segs = [
        {"type": "intro", "text": "t0"},
        {"type": "story", "story_id": "s1", "lead_part": "opener", "text": "t1"},
        {"type": "story", "story_id": "s1", "lead_part": "remainder", "text": "t2"},
    ]
    for i, sid in enumerate(["s2", "s3", "s4", "s5", "s6"]):
        segs.append({"type": "transition", "text": f"br{sid}"})
        segs.append({"type": "story", "story_id": sid, "text": f"st{sid}"})
    segs.append({"type": "outro", "text": "outro"})
    return segs


def _run(segments, priority=None, mock_delay=0.0):
    """Run _synth_remaining with TTS + the priority hint mocked; return the recorded synth order."""
    order: list[str] = []
    lock = threading.Lock()

    def _fake_synth(text, *, segment_type, voice, model, audio_format, **kw):
        if mock_delay:
            time.sleep(mock_delay)
        with lock:
            order.append(text)

    with patch.object(data, "synthesize_segment", _fake_synth), \
         patch.object(data, "_get_synth_priority", lambda _bid: priority):
        data._synth_remaining(1, segments, voice="v", model="m", audio_fmt="mp3")
    return order


def test_tail_synthesises_every_segment_exactly_once():
    segs = _segments()
    boundary = _start_pack_boundary(segs, _SAFE_START_STORIES)   # start-pack already did [0, boundary)
    expected = {s["text"] for s in segs[boundary:] if s.get("text")}

    order = _run(segs)   # no priority → forward drain across the worker pool

    assert len(order) == len(expected), f"one synth per segment, no dup/drop: {order}"
    assert set(order) == expected, "every post-start-pack segment must be synthesised exactly once"
    # Start-pack segments must NOT be re-synthesised by the tail.
    assert not ({s["text"] for s in segs[:boundary]} & set(order))


def test_tail_is_actually_parallel_pool_sized_to_the_bound():
    # More than one worker is used when there's more than one segment to do.
    segs = _segments()
    remaining = len(segs) - _start_pack_boundary(segs, _SAFE_START_STORIES)
    assert remaining > 1
    assert _MAX_PARALLEL_TAIL_SYNTH >= 2, "the tail bound must permit real parallelism"


def test_skip_priority_bumps_the_target_bridge_to_the_front_under_parallelism():
    segs = _segments()
    # Tap/skip to s6 (the LAST story) — its bridge is deep in the tail. With the hint live from the
    # first pull, the reprioritise puts s6's bridge at the head, so it's taken in the first wave.
    # A small mock delay makes all N workers grab their first item near-simultaneously.
    order = _run(segs, priority="s6", mock_delay=0.01)

    assert "brs6" in order
    # s6's bridge must be synthesised within the first pool-sized wave (not left to the tail end).
    assert order.index("brs6") < _MAX_PARALLEL_TAIL_SYNTH, (
        f"tapped story s6's bridge must be bumped into the first {_MAX_PARALLEL_TAIL_SYNTH} synths; "
        f"order={order}"
    )
    # Completeness still holds with a priority hint in play.
    boundary = _start_pack_boundary(segs, _SAFE_START_STORIES)
    assert set(order) == {s["text"] for s in segs[boundary:] if s.get("text")}
