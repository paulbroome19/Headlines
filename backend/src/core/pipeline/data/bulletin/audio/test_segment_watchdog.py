"""Stalled-segment watchdog: pending past the window is stalled; terminal states never are."""
from core.pipeline.data.bulletin.audio.segment_watchdog import is_stalled, SEGMENT_STALL_SECONDS


def test_pending_past_threshold_is_stalled():
    assert is_stalled("pending", SEGMENT_STALL_SECONDS + 1) is True


def test_pending_within_threshold_is_not_stalled():
    # A slow-but-working synth (retries/timeouts, up to ~77s) is never falsely reaped.
    assert is_stalled("pending", 60.0) is False
    assert is_stalled("pending", SEGMENT_STALL_SECONDS - 0.001) is False


def test_terminal_states_never_stall():
    # A segment that landed (ready) or genuinely failed is terminal — the watchdog leaves it alone,
    # so a failed-then-retried-successfully segment (now 'ready') is never re-failed.
    assert is_stalled("ready", 10_000) is False
    assert is_stalled("failed", 10_000) is False


def test_threshold_is_above_max_legitimate_synth_time():
    # 25s timeout × 3 attempts + backoff ≈ 77s; the window must sit above it.
    assert SEGMENT_STALL_SECONDS > 25 * 3 + 2
