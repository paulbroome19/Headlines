"""
Outro builder for bulletins.

Structure (when all three parts are used):
  [caught_up_phrase] [forward_phrase] [signoff_phrase]

Variety comes from:
  - Different caught-up phrasings (8+)
  - Time-of-day aware forward phrases (8+)
  - Occasional short 2-part outros (no signoff)
  - Occasional 1-part outros (just caught-up, clean stop)
  - Name used in roughly 1 in 2 outros — not every time
"""
from __future__ import annotations

import random
from datetime import datetime

# ── Caught-up phrases ──────────────────────────────────────────────────────────

_CAUGHT_UP_WITH_NAME_GENERIC: list[str] = [
    "And that's you caught up, {name}.",
    "Right — you're up to date, {name}.",
    "Done — you're current, {name}.",
    "That's the lot, {name}.",
    "And you're all caught up, {name}.",
    "That's everything for now, {name}.",
    "There we go, {name} — all caught up.",
]

_CAUGHT_UP_NO_NAME: list[str] = [
    "Right — you're up to date.",
    "And that's the lot.",
    "You're all caught up.",
    "Done. You're current.",
    "That's everything for now.",
    "And there you have it.",
    "That's your briefing.",
    "And that's the briefing.",
]


def _caught_up_with_name(rng: "random.Random", name: str, story_count: int) -> str:
    pool = list(_CAUGHT_UP_WITH_NAME_GENERIC)
    if story_count >= 2:
        from core.pipeline.data.bulletin.intros import _count_word
        count = _count_word(story_count)
        pool.append(f"{count.capitalize()} and you're sorted, {name}.")
    template = rng.choice(pool)
    return template.format(name=name)

# ── Forward-looking phrases (time-of-day aware) ────────────────────────────────

_FORWARD_MORNING: list[str] = [
    "Catch you this evening.",
    "Back later if anything breaks.",
    "Have a good one.",
    "More as the day develops.",
    "I'll catch you this evening.",
    "Have a good morning.",
]

_FORWARD_AFTERNOON: list[str] = [
    "Talk soon.",
    "Back this evening.",
    "More later.",
    "Have a good afternoon.",
    "I'll catch you this evening.",
    "More when there's more.",
]

_FORWARD_EVENING: list[str] = [
    "I'll catch you in the morning.",
    "More tomorrow.",
    "Have a quiet night.",
    "Until the morning.",
    "Sleep well.",
    "More as it comes in.",
]

_FORWARD_ANY: list[str] = [
    "Until next time.",
    "More when there's more.",
    "Talk soon.",
    "Back soon.",
]

# ── Sign-off (brief, occasional) ──────────────────────────────────────────────

_SIGNOFFS: list[str] = [
    "Take care.",
    "Cheers.",
    "Until then.",
    "Good day.",
    "",   # blank = no signoff (weight toward shorter outros)
    "",
    "",
]


def _forward_phrase(rng: random.Random, hour: int) -> str:
    if hour < 12:
        pool = _FORWARD_MORNING
    elif hour < 17:
        pool = _FORWARD_AFTERNOON
    elif hour < 22:
        pool = _FORWARD_EVENING
    else:
        pool = _FORWARD_EVENING + _FORWARD_ANY
    return rng.choice(pool)


# ── Public API ─────────────────────────────────────────────────────────────────

def build_outro(
    rng: random.Random,
    *,
    name: str | None,
    now: datetime,
    story_count: int = 0,
) -> str:
    """
    Build a bulletin outro.

    Varies structure: 1-part (caught-up only), 2-part (caught-up + forward),
    or 3-part (caught-up + forward + signoff).
    Name used in roughly half of outros.
    """
    hour = now.hour
    use_name = bool(name) and rng.random() < 0.55

    # Caught-up phrase
    if use_name:
        caught_up = _caught_up_with_name(rng, name, story_count)
    else:
        caught_up = rng.choice(_CAUGHT_UP_NO_NAME)

    # Structure roll
    roll = rng.random()
    if roll < 0.20:
        # 1-part: caught up only — clean, confident stop
        return caught_up

    forward = _forward_phrase(rng, hour)

    if roll < 0.65:
        # 2-part: caught up + forward
        return f"{caught_up} {forward}"

    # 3-part: caught up + forward + signoff
    signoff = rng.choice(_SIGNOFFS)
    if signoff:
        return f"{caught_up} {forward} {signoff}"
    return f"{caught_up} {forward}"
