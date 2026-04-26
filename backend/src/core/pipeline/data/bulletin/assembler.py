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


# ── Intro/outro pools ──────────────────────────────────────────────────────────

def _build_intro(rng: random.Random, name: str | None, now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    # US date format to match product spec: "Saturday, April 25"
    month_day = now.strftime(f"%B {now.day}")   # e.g. "April 25"
    weekday = now.strftime("%A")                 # e.g. "Saturday"
    date_str = f"{weekday}, {month_day}"

    if name:
        templates = [
            f"{greeting}, {name}. Here's your news briefing for {date_str}.",
            f"{greeting}, {name}. Here's what's happening on {date_str}.",
        ]
    else:
        templates = [
            f"Here's your news briefing for {date_str}.",
            f"Your news update for {date_str}.",
            f"Here's what's happening today.",
        ]
    return rng.choice(templates)


def _build_outro(rng: random.Random, name: str | None) -> str:
    if name:
        templates = [
            f"That's your briefing for now, {name}.",
            f"Thanks for listening, {name}.",
            f"That's all for now, {name}.",
        ]
    else:
        templates = [
            "That's your briefing for now.",
            "That's all for now.",
            "Thanks for listening.",
            "That's all for today.",
        ]
    return rng.choice(templates)


# ── Transition pools ───────────────────────────────────────────────────────────

_CATEGORY_TRANSITIONS: dict[str, list[str]] = {
    "politics":      ["In politics...", "On the political front...", "In political news..."],
    "world":         ["Elsewhere...", "Around the world...", "Internationally..."],
    "business":      ["In business news...", "On the business front...", "In the markets..."],
    "technology":    ["In technology...", "On the tech front...", "In tech news..."],
    "sport":         ["In sport...", "On the sporting front...", "In sports news..."],
    "entertainment": ["In entertainment...", "On the entertainment front..."],
    "health":        ["In health news...", "On the health front..."],
    "science":       ["In science...", "In science news..."],
    "climate":       ["In climate news...", "On the climate front..."],
}

_NEUTRAL_TRANSITIONS: list[str] = [
    "In other news...",
    "Meanwhile...",
    "Elsewhere...",
    "And now...",
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
    segments.append({"type": "intro", "text": _build_intro(rng, name, now)})

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
    segments.append({"type": "outro", "text": _build_outro(rng, name)})

    script = "\n\n".join(seg["text"] for seg in segments)
    return BulletinResult(script=script, segments=segments)
