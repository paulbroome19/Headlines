"""
Stalled-segment watchdog.

A segment's TTS runs in the background with bounded retries; on genuine failure it records a
`segment_audio_failures` row, and the readiness gate / `/segments` surface that (loader shows an
error, iOS can skip). But if the synth **stalls** — the worker thread dies silently, ElevenLabs
hangs past the request timeout in a way that never returns, or the process restarts mid-synth —
NO failure row is ever written. The segment stays `pending` forever: the loader's safe_to_start
can never fire AND there's no failure to surface, so the spinner hangs indefinitely (the reported
bug, seen on smaller categories where more segments are freshly synthesised rather than cached).

The watchdog makes a stall DETERMINISTIC: a gate segment still pending well past the longest a
legitimate synth could take is flipped to `failed`, so the existing failure path fires.

Threshold: bounded synth time is ~= _TTS_TIMEOUT (25s) × _TTS_MAX_ATTEMPTS (3) + backoff (~2s)
≈ 77s. SEGMENT_STALL_SECONDS sits above that so a slow-but-working synth is never falsely reaped —
only one that has demonstrably stopped making progress.
"""
from __future__ import annotations

SEGMENT_STALL_SECONDS = 90.0


def is_stalled(state: str, age_seconds: float, threshold: float = SEGMENT_STALL_SECONDS) -> bool:
    """True if a segment in the given readiness `state` has been outstanding long enough to be
    considered stalled. Only `pending` can stall — `ready` and `failed` are terminal. `age_seconds`
    is how long ago the bulletin was assembled (when the synth was enqueued)."""
    return state == "pending" and age_seconds > threshold
