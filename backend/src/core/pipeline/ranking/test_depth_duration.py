"""
Depth-by-rank duration budget.

A Medium briefing is ~10 stories (1 lead + 2 major + 4 standard + 3 brief). The depth word
targets are calibrated for the PODCAST voice (see core.pipeline.data.voice.VOICE_BLOCK) —
a warm spoken catch-up, deliberately tight, "not an essay" — which lands a Medium at ~5.5
min, not the ~10 min of the old essay-length targets. These tests pin the budget to that
register and guard against regressing to essay lengths. Speech rate measured on prod ≈ 156
wpm; wrappers (greeting + ~9 bridges + outro) add ~270 words.
"""
from __future__ import annotations

from .depth import depth_words_for_rank

# A representative Standard briefing length for the duration budget (the threshold +
# filter-order model sizes by depth×breadth, not a fixed count; a typical multi-category
# Standard lands around here).
_STANDARD_STORIES = 10

_WPM = 156.0                 # measured ElevenLabs rate (bulletin #103: 882 words / 5:39)
_WRAPPER_WORDS = 270         # intro + ~9 transitions + outro for a ~10-story bulletin


def _story_words(n_stories: int) -> int:
    return sum(depth_words_for_rank(r) for r in range(n_stories))


def test_medium_briefing_targets_the_podcast_budget():
    n = _STANDARD_STORIES   # 10
    total_words = _story_words(n) + _WRAPPER_WORDS
    minutes = total_words / _WPM
    # The podcast voice lands a Medium at ~5.5 min (595 story + 270 wrapper ≈ 865 words).
    # Allow 4.5–6.5 for LLM variance / wpm drift. This range excludes both the old essay
    # targets (~10 min) and an accidental collapse to a fact-list (<4.5 min).
    assert 4.5 <= minutes <= 6.5, f"Medium ≈ {minutes:.1f} min ({total_words} words)"


def test_lead_is_the_deepest_tier():
    words = [depth_words_for_rank(r) for r in range(10)]
    assert words[0] == max(words)           # rank 0 (lead) is deepest
    assert words == sorted(words, reverse=True)  # non-increasing by rank


def test_old_essay_targets_would_overshoot_the_budget():
    # Guard: the old essay-length targets (260/180/110/55) summed to ~1225 story words ≈
    # ~9.6 min — far above the podcast budget. This is the regression to avoid: the voice
    # is a spoken catch-up, not a read-aloud essay.
    old = {"lead": 260, "major": 180, "standard": 110, "brief": 55}
    bands = [old["lead"]] + [old["major"]] * 2 + [old["standard"]] * 4 + [old["brief"]] * 3
    minutes = (sum(bands) + _WRAPPER_WORDS) / _WPM
    assert minutes > 6.5


# ── Home-preview minutes estimate: calibrated to real briefings + scales ─────────

from core.api.routes.data import _preview_minutes


def test_preview_minutes_scales_and_is_reasonable():
    # Monotonic in story count, and no wild over-estimate on small briefings (the bug:
    # 6 stories estimated 7m for a real ~5m). Calibrated model tracks real within ~1 min.
    vals = {n: _preview_minutes(n) for n in (0, 5, 8, 12, 16, 20)}
    assert vals[0] == 0
    assert vals[5] <= 6 and vals[12] <= 10 and vals[20] <= 14     # not the old 7/11/14 over-estimate
    seq = [vals[n] for n in (5, 8, 12, 16, 20)]
    assert seq == sorted(seq)                                     # non-decreasing
    assert seq[-1] - seq[0] >= 6                                  # genuinely scales (5→20 grows)
