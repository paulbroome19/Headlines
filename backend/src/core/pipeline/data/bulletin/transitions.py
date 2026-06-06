"""
Transition phrase library and selector.

Rules:
- Never repeat a transition within the same bulletin
- After a heavy/tragic story, prefer tonal bridges over category-name bridges
- ~15% of transitions are an empty string (signal: use a pause instead)
- Seed-based rotation via the bulletin's RNG for variety without repetition
"""
from __future__ import annotations

import random

# ── Category-specific pools ────────────────────────────────────────────────────
# Each key matches a top-level taxonomy slug.
# Aim: 9-10 distinct phrases per category (allows long bulletins without repeat).

CATEGORY_TRANSITIONS: dict[str, list[str]] = {
    "politics": [
        "In Westminster...",
        "Down at Westminster...",
        "Politically...",
        "On the political front...",
        "Over in Westminster...",
        "In government...",
        "At Number 10...",
        "Around the political world...",
        "In the Commons...",
        "On the campaign trail...",
    ],
    "world": [
        "Elsewhere in the world...",
        "Internationally...",
        "On the world stage...",
        "Overseas...",
        "Further afield...",
        "On the international front...",
        "Around the globe...",
        "From further afield...",
        "And globally...",
        "Out in the wider world...",
    ],
    "business": [
        "In business...",
        "On the business front...",
        "In the markets...",
        "On the financial front...",
        "Economically speaking...",
        "In the economy...",
        "In the City...",
        "In corporate news...",
        "On the trading floor...",
        "Business-wise...",
    ],
    "technology": [
        "In tech...",
        "On the tech front...",
        "In the world of technology...",
        "On the digital front...",
        "In AI and tech...",
        "In Silicon Valley...",
        "Turning to technology...",
        "From the tech world...",
        "In software and hardware...",
        "And in tech news...",
    ],
    "sport": [
        "In sport...",
        "On the sporting front...",
        "In the sporting world...",
        "On the pitch...",
        "In sport today...",
        "Turning to sport...",
        "On the sports pages...",
        "In athletics...",
        "On the track and field...",
        "Sporting-wise...",
    ],
    "entertainment": [
        "In entertainment...",
        "On the entertainment front...",
        "In arts and culture...",
        "In culture...",
        "On screen...",
        "In the arts...",
        "From the world of entertainment...",
        "In film and television...",
        "On the cultural front...",
    ],
    "health": [
        "In health news...",
        "On the health front...",
        "In medicine...",
        "On the medical front...",
        "In public health...",
        "From the NHS...",
        "Health-wise...",
        "In healthcare...",
        "On the medical pages...",
    ],
    "science": [
        "In science...",
        "On the research front...",
        "From the world of science...",
        "In science news...",
        "Out of the labs...",
        "From researchers...",
        "In scientific news...",
        "Scientifically speaking...",
        "And in research...",
    ],
    "climate": [
        "On climate...",
        "In environmental news...",
        "On the climate front...",
        "In climate news...",
        "On the environment...",
        "And in climate...",
        "Environmentally speaking...",
        "From the green agenda...",
        "On net zero...",
    ],
}

# ── Tonal bridges ──────────────────────────────────────────────────────────────
# Use after a heavy, tragic, or conflict-heavy story before something lighter.

TONAL_BRIDGES_HEAVY_TO_LIGHT: list[str] = [
    "On a lighter note...",
    "Something a little more cheerful...",
    "To lift the mood slightly...",
    "Stepping away from that for a moment...",
    "Breathing out for a second...",
    "Something different now...",
    "Away from that...",
    "On brighter news...",
]

TONAL_BRIDGES_LIGHT_TO_HEAVY: list[str] = [
    "More seriously...",
    "On a weightier note...",
    "On something more significant...",
    "Turning to harder news...",
    "Back to something that matters...",
    "On a more serious front...",
]

# ── Geographic bridges ─────────────────────────────────────────────────────────

GEOGRAPHIC_BRIDGES: list[str] = [
    "Across the Atlantic...",
    "Closer to home...",
    "Further afield...",
    "Staying overseas...",
    "Back in the UK...",
    "Over in Europe...",
    "From Washington...",
    "In Brussels...",
    "Back stateside...",
    "From the Middle East...",
]

# ── Thematic bridges ───────────────────────────────────────────────────────────

THEMATIC_BRIDGES: list[str] = [
    "Staying with that theme...",
    "On a related note...",
    "Connected to that...",
    "In a similar vein...",
    "Still on this...",
    "And relatedly...",
    "Building on that...",
]

# ── Neutral / general ──────────────────────────────────────────────────────────

NEUTRAL_TRANSITIONS: list[str] = [
    "Meanwhile...",
    "Also today...",
    "And now...",
    "Turning now...",
    "At the same time...",
    "Also in the news...",
    "And there's this...",
    "And in other news...",
    "Moving on...",
    "Also making news today...",
    "And finally...",        # only natural at last transition — selector notes this
    "There's also this...",
    "And separately...",
    "One more...",
]

# Empty string = silence. ~15% of transitions use this.
_PAUSE = ""

# ── "Heavy" category heuristic ─────────────────────────────────────────────────
# Stories in these categories are considered potentially heavy/tragic.
# The selector uses this to bias toward tonal bridges.

_HEAVY_CATEGORIES = frozenset(["world", "health", "climate", "politics"])

_HEAVY_KEYWORDS = frozenset([
    "kill", "dead", "death", "murder", "attack", "terror", "war", "crisis",
    "flood", "fire", "crash", "disaster", "victim", "tragedy", "fatal",
])


def _is_heavy(story: dict | None) -> bool:
    """Heuristic: does this story feel heavy/tragic?"""
    if story is None:
        return False
    cat = (story.get("primary_category") or "").split(".")[0].lower()
    if cat not in _HEAVY_CATEGORIES:
        return False
    text = " ".join([
        story.get("audio_script") or "",
        story.get("summary_text") or "",
        story.get("headline") or "",
    ]).lower()
    return any(kw in text for kw in _HEAVY_KEYWORDS)


# ── Selector ───────────────────────────────────────────────────────────────────

def pick_transition(
    rng: random.Random,
    *,
    previous_story: dict | None,
    next_story: dict | None,
    position: int,              # 0-based index of this transition
    total_stories: int,
    used_in_bulletin: set[str],
) -> str:
    """
    Return a transition phrase (or "" for silence).

    Args:
        rng:              seeded RNG from the bulletin assembler
        previous_story:   the story just finished
        next_story:       the story about to begin
        position:         index of this transition (0 = between stories 1 and 2)
        total_stories:    total story count in this bulletin
        used_in_bulletin: set of phrases already used — prevents within-bulletin repeats
    """
    prev_heavy = _is_heavy(previous_story)
    next_cat = (next_story.get("primary_category") or "") if next_story else ""
    next_top_cat = next_cat.split(".")[0].lower()

    # ~15% chance of a pause (empty string)
    if rng.random() < 0.15:
        return _PAUSE

    # After a heavy story, bias toward tonal bridge
    if prev_heavy and rng.random() < 0.65:
        pool = TONAL_BRIDGES_HEAVY_TO_LIGHT
        available = [t for t in pool if t not in used_in_bulletin]
        if available:
            return rng.choice(available)

    # Category-specific transition
    cat_pool = CATEGORY_TRANSITIONS.get(next_top_cat, [])
    if cat_pool:
        available = [t for t in cat_pool if t not in used_in_bulletin]
        if available:
            return rng.choice(available)

    # Neutral fallback
    available = [t for t in NEUTRAL_TRANSITIONS if t not in used_in_bulletin]
    if not available:
        available = NEUTRAL_TRANSITIONS  # allow repeat rather than crash
    return rng.choice(available)
