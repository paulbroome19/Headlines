"""
Bulletin assembler — pure assembly, no LLM, no IO.

Takes an ordered list of story dicts and produces a BulletinResult
containing the full script (pronunciation-normalised) and a structured
segments list.

Dependencies:
  intros.py       — personalised opening builder
  transitions.py  — 80+ transition library with selector
  outros.py       — closing builder
  pronunciation.py — pre-TTS normalisation (numbers, acronyms, names)
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

from core.pipeline.data.bulletin.intros import build_intro
from core.pipeline.data.bulletin.transitions import pick_transition
from core.pipeline.data.bulletin.outros import build_outro
from core.pipeline.data.bulletin.pronunciation import normalise_for_tts


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
    is_first_bulletin: bool = False,
) -> BulletinResult:
    """
    Assemble a bulletin from an ordered list of story dicts.

    Each story dict must contain:
      story_id:         str
      audio_script:     str   (spoken text; falls back to summary_text if empty)
      primary_category: str | None

    seed              — pins all random choices; pass int(request_hash, 16).
    name              — personalises intro and outro.
    now               — datetime for time-of-day logic; defaults to UTC now.
    is_first_bulletin — True on the user's very first bulletin.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    rng = random.Random(seed)
    segments: list[dict] = []

    # ── Intro ──────────────────────────────────────────────────────────────────
    intro_text = build_intro(
        rng,
        name=name,
        now=now,
        stories=stories,
        is_first_bulletin=is_first_bulletin,
    )
    segments.append({"type": "intro", "text": intro_text})

    # ── Stories with transitions ───────────────────────────────────────────────
    used_transitions: set[str] = set()

    for i, story in enumerate(stories):
        if i > 0:
            transition_text = pick_transition(
                rng,
                previous_story=stories[i - 1],
                next_story=story,
                position=i - 1,
                total_stories=len(stories),
                used_in_bulletin=used_transitions,
            )
            used_transitions.add(transition_text)
            segments.append({"type": "transition", "text": transition_text})

        story_text = (story.get("audio_script") or "").strip() or (story.get("summary_text") or "")
        segments.append({
            "type": "story",
            "story_id": story["story_id"],
            "text": story_text,
        })

    # ── Outro ──────────────────────────────────────────────────────────────────
    outro_text = build_outro(
        rng,
        name=name,
        now=now,
        story_count=len(stories),
    )
    segments.append({"type": "outro", "text": outro_text})

    # ── Assemble script + pronunciation normalisation ──────────────────────────
    raw_script = "\n\n".join(seg["text"] for seg in segments if seg["text"])
    script = normalise_for_tts(raw_script)

    return BulletinResult(script=script, segments=segments)
