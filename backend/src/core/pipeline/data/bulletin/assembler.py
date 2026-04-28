"""
Bulletin assembler — pure assembly, no LLM, no IO.

Takes an ordered list of story dicts and produces a BulletinResult
containing the full script and a structured segments list.

Personalisation:
  name  — if provided, intro/outro greet by name
  now   — datetime used for date + time-of-day greeting; defaults to UTC now

Transitions:
  - category-specific pools (multiple templates per category top-level)
  - neutral fallback pool
  - anti-repeat: never uses the same transition text twice in a row
  - seeded RNG for reproducibility
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone


# ── Intro helpers ──────────────────────────────────────────────────────────────

def _extract_hook(story: dict) -> str:
    """Pull the first sentence of a story's audio_script as an intro hook."""
    text = (story.get("audio_script") or story.get("summary_text") or "").strip()
    for end in (". ", ".\n"):
        idx = text.find(end)
        if 20 <= idx <= 200:
            return text[:idx]
    # No clean sentence boundary — truncate without breaking a word
    if len(text) > 160:
        return text[:160].rsplit(" ", 1)[0]
    return text


def _build_intro(rng: random.Random, name: str | None, now: datetime, stories: list[dict]) -> str:
    hour = now.hour
    if hour < 12:
        time_greeting = "Good morning"
    elif hour < 17:
        time_greeting = "Good afternoon"
    else:
        time_greeting = "Good evening"

    month_day = now.strftime(f"%B {now.day}")
    weekday = now.strftime("%A")
    date_str = f"{weekday}, {month_day}"

    hook = _extract_hook(stories[0]) if stories else ""

    if hook:
        bridge = rng.choice([
            "That story leads the briefing.",
            "We begin there.",
            "That's where we start.",
            "That leads today.",
            "That's what we're opening with.",
        ])
        if name:
            return rng.choice([
                f"{hook}. {bridge} {time_greeting}, {name}.",
                f"{time_greeting}, {name}. {hook}. {bridge}",
            ])
        else:
            return rng.choice([
                f"{hook}. {bridge}",
                f"Here's what's leading the news: {hook}. {bridge}",
            ])

    # Fallback: date-based generic intro
    if name:
        return rng.choice([
            f"{time_greeting}, {name}. Here's your news briefing for {date_str}.",
            f"{time_greeting}, {name}. Here's what's happening on {date_str}.",
        ])
    return rng.choice([
        f"Here's your news briefing for {date_str}.",
        f"Your news update for {date_str}.",
        f"Here's what's happening today.",
    ])


# ── Outro ──────────────────────────────────────────────────────────────────────

def _build_outro(rng: random.Random, name: str | None, now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        time_label = "morning"
    elif hour < 17:
        time_label = "afternoon"
    else:
        time_label = "evening"

    if name:
        templates = [
            f"We'll keep following these stories as they develop, {name}.",
            f"More on all of this as it comes in. That's your briefing, {name}.",
            f"That's the {time_label} briefing, {name}. More updates throughout the day.",
            f"That's what's making news this {time_label}, {name}. More as it develops.",
            f"That's it for now, {name}. We'll be back with more as these stories move.",
            f"Those are the headlines, {name}. We'll have more as the day progresses.",
        ]
    else:
        templates = [
            "We'll have more as these stories develop.",
            "That's the briefing for now. More updates as they come.",
            f"That's what's making news this {time_label}.",
            "More on all of this as it develops.",
            f"That's your {time_label} briefing. Stay across the stories.",
            "That's the news for now.",
        ]
    return rng.choice(templates)


# ── Transition pools ───────────────────────────────────────────────────────────

_CATEGORY_TRANSITIONS: dict[str, list[str]] = {
    "politics": [
        "In politics...",
        "On the political front...",
        "In political news...",
        "Turning to politics...",
        "In government...",
        "Politically...",
        "In Westminster...",
        "In Washington...",
    ],
    "world": [
        "Elsewhere...",
        "Around the world...",
        "Internationally...",
        "Turning to international news...",
        "On the world stage...",
        "Overseas...",
        "Further afield...",
        "On the international front...",
    ],
    "business": [
        "In business news...",
        "On the business front...",
        "In the markets...",
        "On the financial front...",
        "In business...",
        "In the economy...",
        "Economically speaking...",
    ],
    "technology": [
        "In technology...",
        "On the tech front...",
        "In tech news...",
        "Turning to technology...",
        "In the world of tech...",
        "On the digital front...",
        "In AI and tech...",
    ],
    "sport": [
        "In sport...",
        "On the sporting front...",
        "In sports news...",
        "Turning to sport...",
        "In the sporting world...",
        "On the pitch...",
        "In sport today...",
    ],
    "entertainment": [
        "In entertainment...",
        "On the entertainment front...",
        "In arts and culture...",
        "In culture...",
        "In the arts...",
    ],
    "health": [
        "In health news...",
        "On the health front...",
        "In medicine...",
        "On the medical front...",
        "In public health...",
    ],
    "science": [
        "In science...",
        "In science news...",
        "On the research front...",
        "In the world of science...",
        "From the world of science...",
    ],
    "climate": [
        "In climate news...",
        "On the climate front...",
        "In environmental news...",
        "On the environment...",
        "On the climate...",
    ],
}

_NEUTRAL_TRANSITIONS: list[str] = [
    "In other news...",
    "Meanwhile...",
    "Elsewhere...",
    "And now...",
    "At the same time...",
    "On a related note...",
    "Turning now...",
    "Also making news...",
    "Away from that...",
    "And there's this...",
    "Also today...",
    "Moving on...",
    "Closer to home...",
    "And in news that's also been moving...",
]


def _pick_transition(
    rng: random.Random,
    category: str | None,
    last_text: str | None,
) -> str:
    """
    Pick a transition phrase for a story.

    Selection order:
    1. Category-specific pool if the top-level category is recognised.
    2. Neutral pool as fallback if category is unknown/missing.
    3. Anti-repeat: exclude last_text from candidates if alternatives exist.
       If category pool is exhausted, expand to include neutral pool.
    """
    cat_key = (category or "").split(".")[0].lower()
    cat_pool = _CATEGORY_TRANSITIONS.get(cat_key, [])
    primary_pool = cat_pool if cat_pool else _NEUTRAL_TRANSITIONS

    # Filter out the last used transition to avoid consecutive repeats.
    available = [t for t in primary_pool if t != last_text]

    if not available:
        # Primary pool is a single entry that would repeat — try neutral as escape.
        if cat_pool:
            available = [t for t in _NEUTRAL_TRANSITIONS if t != last_text]
        # If still empty (everything would repeat), allow the repeat.
        if not available:
            available = primary_pool

    return rng.choice(available)


# ── Public API ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BulletinResult:
    script: str
    segments: list[dict]


def assemble(
    stories: list[dict],
    *,
    seed: int | None = None,
    name: str | None = None,
    now: datetime | None = None,
) -> BulletinResult:
    """
    Assemble a bulletin from an ordered list of story dicts.

    Each story dict must contain:
      story_id:         str
      audio_script:     str   (spoken text; falls back to summary_text if empty)
      primary_category: str | None

    seed  — pins all random choices; pass int(request_hash, 16) for determinism.
    name  — personalises intro and outro.
    now   — datetime for date/time-of-day greeting; defaults to UTC now.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    rng = random.Random(seed)
    segments: list[dict] = []

    # ── Intro ──────────────────────────────────────────────────────────────────
    segments.append({"type": "intro", "text": _build_intro(rng, name, now, stories)})

    # ── Stories with transitions ───────────────────────────────────────────────
    last_transition: str | None = None
    for i, story in enumerate(stories):
        if i > 0:
            cat = story.get("primary_category")
            transition_text = _pick_transition(rng, cat, last_transition)
            last_transition = transition_text
            segments.append({"type": "transition", "text": transition_text})

        story_text = (story.get("audio_script") or "").strip() or (story.get("summary_text") or "")
        segments.append({
            "type": "story",
            "story_id": story["story_id"],
            "text": story_text,
        })

    # ── Outro ──────────────────────────────────────────────────────────────────
    segments.append({"type": "outro", "text": _build_outro(rng, name, now)})

    script = "\n\n".join(seg["text"] for seg in segments)
    return BulletinResult(script=script, segments=segments)
