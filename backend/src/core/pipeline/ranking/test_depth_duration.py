"""
Depth-by-rank duration budget.

A Medium briefing is ~10 stories (1 lead + 2 major + 4 standard + 3 brief). The depth
word targets must sum to roughly a 10-minute briefing — the bug this guards is targets
calibrated for ~5.5 min (the summaries hit their targets; the targets were half of what
~10 min needs). Speech rate measured on prod ≈ 156 wpm; wrappers (intro + ~9 transitions
+ outro) add ~270 words.
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


def test_medium_briefing_targets_about_ten_minutes():
    n = _STANDARD_STORIES   # 10
    total_words = _story_words(n) + _WRAPPER_WORDS
    minutes = total_words / _WPM
    # Near ~10 min (allow 8.5–11.5 for LLM variance / wpm drift). The old targets gave
    # ~5.5 min, which this range excludes.
    assert 8.5 <= minutes <= 11.5, f"Medium ≈ {minutes:.1f} min ({total_words} words)"


def test_lead_is_the_deepest_tier():
    words = [depth_words_for_rank(r) for r in range(10)]
    assert words[0] == max(words)           # rank 0 (lead) is deepest
    assert words == sorted(words, reverse=True)  # non-increasing by rank


def test_old_short_targets_would_fail_the_budget():
    # Guard: the previous targets (120/80/50/25) summed to ~555 story words ≈ ~5.3 min —
    # below the acceptable window, which is exactly the regression.
    old = {"lead": 120, "major": 80, "standard": 50, "brief": 25}
    bands = [old["lead"]] + [old["major"]] * 2 + [old["standard"]] * 4 + [old["brief"]] * 3
    minutes = (sum(bands) + _WRAPPER_WORDS) / _WPM
    assert minutes < 8.5
