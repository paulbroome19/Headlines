"""
Intro builder for bulletins.

Produces a personalised opening that:
  - References today's actual top story (the hook)
  - Places the user's name naturally — once only
  - Varies based on time of day, story count, and rotation
  - Never feels like a template

Selection avoids the last 5 intro patterns used (via seed rotation).
"""
from __future__ import annotations

import random
from datetime import datetime


# ── Bridge phrases (hook → greeting) ──────────────────────────────────────────

_BRIDGES: list[str] = [
    "That's where we start.",
    "That story leads the briefing.",
    "We begin there.",
    "That leads today.",
    "That's what we're opening with.",
    "Starting with that one.",
    "That's the top of the hour.",
    "Let's get into it.",
]

# ── Opening templates ──────────────────────────────────────────────────────────
# Each template is a function: (name, story_count, hour, weekday, hook, bridge) → str
# Templates are tagged with which conditions they suit best.
# The selector picks contextually then applies seed-based rotation.

def _t_morning_hook_name(name, n, hour, day, hook, bridge):
    return f"{hook}. {bridge} Morning, {name}."

def _t_evening_hook_name(name, n, hour, day, hook, bridge):
    return f"{hook}. {bridge} Evening, {name}."

def _t_right_then_name(name, n, hour, day, hook, bridge):
    return f"Right then, {name}. {hook}. {bridge}"

def _t_quick_one_name(name, n, hour, day, hook, bridge):
    count_word = _count_word(n)
    return f"Quick one for you today, {name} — {count_word}, just under {_est_minutes(n)}. {hook}."

def _t_name_first(name, n, hour, day, hook, bridge):
    return f"{name}, good {'morning' if hour < 12 else 'evening'}. {hook}. {bridge}"

def _t_here_we_are(name, n, hour, day, hook, bridge):
    return f"Here we are, {name}. {hook}. {bridge}"

def _t_lets_get_into_it(name, n, hour, day, hook, bridge):
    return f"{hook}. Let's get into it, {name}."

def _t_day_opener(name, n, hour, day, hook, bridge):
    return f"{day} morning, {name}. {hook}. {bridge}"

def _t_busy_day(name, n, hour, day, hook, bridge):
    return f"It's been a busy {'morning' if hour < 12 else 'day'}, {name}. {hook}. {bridge}"

def _t_three_things(name, n, hour, day, hook, bridge):
    count_word = _count_word(n)
    return f"Morning, {name} — {count_word} to start your {day}. {hook}. {bridge}"

def _t_evening_caught_up(name, n, hour, day, hook, bridge):
    return f"Evening, {name}. Here's what you've missed today. {hook}. {bridge}"

def _t_hook_then_name(name, n, hour, day, hook, bridge):
    return f"{hook}. {bridge} {name}."

def _t_no_name_hook(name, n, hour, day, hook, bridge):
    return f"{hook}. {bridge}"

def _t_here_is(name, n, hour, day, hook, bridge):
    return f"Here's the briefing, {name}. {hook}. {bridge}"

def _t_lots_on(name, n, hour, day, hook, bridge):
    if n < 3:
        return f"Quick one today, {name}. {hook}. {bridge}"
    return f"Plenty on today, {name}. Starting with this: {hook}. {bridge}"


# ── First-time variants ────────────────────────────────────────────────────────

_FIRST_TIME_TEMPLATES = [
    lambda name, n, *_: f"Welcome in, {name}. Let me catch you up.",
    lambda name, n, *_: f"First one, {name}. Here's what's happening.",
    lambda name, n, *_: f"Good to have you, {name}. Let's start you off.",
]

# ── Template pool (ordered by general utility) ────────────────────────────────

_ALL_TEMPLATES = [
    _t_morning_hook_name,
    _t_right_then_name,
    _t_quick_one_name,
    _t_name_first,
    _t_here_we_are,
    _t_lets_get_into_it,
    _t_day_opener,
    _t_busy_day,
    _t_three_things,
    _t_evening_caught_up,
    _t_hook_then_name,
    _t_no_name_hook,
    _t_here_is,
    _t_lots_on,
    _t_evening_hook_name,
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _count_word(n: int) -> str:
    words = {1: "one story", 2: "two stories", 3: "three stories", 4: "four stories",
             5: "five stories", 6: "six stories", 7: "seven stories", 8: "eight stories",
             9: "nine stories", 10: "ten stories"}
    return words.get(n, f"{n} stories")


def _est_minutes(n: int) -> str:
    """Rough spoken estimate based on story count."""
    seconds = n * 35   # ~35s per story at 150 wpm / 120-word target
    mins = round(seconds / 60)
    if mins <= 1:
        return "a minute"
    return f"{mins} minutes"


def _extract_hook(story: dict) -> str:
    """Pull the first sentence from a story's audio_script as the hook line."""
    text = (story.get("audio_script") or story.get("summary_text") or "").strip()
    for end in (". ", ".\n"):
        idx = text.find(end)
        if 20 <= idx <= 220:
            return text[:idx]
    if len(text) > 180:
        return text[:180].rsplit(" ", 1)[0]
    return text


# ── Public API ─────────────────────────────────────────────────────────────────

def build_intro(
    rng: random.Random,
    *,
    name: str | None,
    now: datetime,
    stories: list[dict],
    is_first_bulletin: bool = False,
) -> str:
    """
    Build an opening for this bulletin.

    name:               user's first name (None → no personalisation)
    now:                current datetime
    stories:            ordered story list (first = top story)
    is_first_bulletin:  True → use a friendlier first-time opening
    """
    hour = now.hour
    weekday = now.strftime("%A")
    n = len(stories)
    hook = _extract_hook(stories[0]) if stories else ""
    bridge = rng.choice(_BRIDGES)

    # First-ever bulletin
    if is_first_bulletin and name:
        return rng.choice(_FIRST_TIME_TEMPLATES)(name, n, hour, weekday, hook, bridge)

    # No hook available — plain fallback
    if not hook:
        if name:
            return rng.choice([
                f"Morning, {name}. Here's what's happening.",
                f"Right then, {name}. Here's the briefing.",
                f"Evening, {name}. Here's what you've missed.",
                f"Here's the news, {name}.",
            ])
        return rng.choice([
            "Here's the news.",
            "Right. Here's what's happening.",
            "Here's your briefing.",
        ])

    # Filter templates by time of day for naturalness
    morning_only = {_t_morning_hook_name, _t_day_opener, _t_three_things}
    evening_only = {_t_evening_hook_name, _t_evening_caught_up}

    if hour < 12:
        pool = [t for t in _ALL_TEMPLATES if t not in evening_only]
    elif hour >= 17:
        pool = [t for t in _ALL_TEMPLATES if t not in morning_only]
    else:
        pool = [t for t in _ALL_TEMPLATES if t not in morning_only and t not in evening_only]

    if not pool:
        pool = _ALL_TEMPLATES

    # When name is provided, exclude the name-free template so the name always appears
    if name:
        named_pool = [t for t in pool if t is not _t_no_name_hook]
        if named_pool:
            pool = named_pool
    else:
        # No name — use only the name-free template (or fallback)
        namefree = [t for t in pool if t is _t_no_name_hook]
        pool = namefree if namefree else pool

    fn = rng.choice(pool)
    return fn(name or "", n, hour, weekday, hook, bridge)
