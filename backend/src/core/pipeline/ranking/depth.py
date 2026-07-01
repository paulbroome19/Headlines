"""
Depth by rank (docs/ranking-depth-design.md).

Higher-ranked (bigger) stories get a fuller treatment; the tail moves briskly —
like a real broadcast. Four coarse tiers, assigned by a story's position in the
final ordered bulletin. Coarse tiers (not continuous word counts) keep the summary
cache to ≤4 rows per story and let presets share cached depths.
"""
from __future__ import annotations

from .config import DEPTH_TIERS, _DEPTH_RANK_BANDS


def depth_for_rank(rank: int) -> tuple[str, int]:
    """
    (tier_name, target_words) for a 0-based rank position.
      rank 0     → lead     (deep)
      ranks 1–2  → major
      ranks 3–6  → standard
      ranks 7+   → brief    (a sentence or two)
    """
    lead_max, major_max, standard_max = _DEPTH_RANK_BANDS
    if rank < lead_max:
        idx = 0
    elif rank < major_max:
        idx = 1
    elif rank < standard_max:
        idx = 2
    else:
        idx = 3
    return DEPTH_TIERS[idx]


def depth_tier_for_rank(rank: int) -> str:
    return depth_for_rank(rank)[0]


def depth_words_for_rank(rank: int) -> int:
    return depth_for_rank(rank)[1]
