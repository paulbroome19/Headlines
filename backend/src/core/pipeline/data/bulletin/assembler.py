"""
Bulletin assembler — pure assembly, no LLM, no IO.

Takes an ordered list of story dicts and produces a BulletinResult
containing the full script and a structured segments list.

Transitions are category-aware (keyed on the first segment of primary_category).
Intro and outro are chosen from small fixed lists, seeded by ranking_run_id
for reproducibility.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

_INTROS = [
    "Here's your latest news briefing.",
    "This is your latest news update.",
]

_OUTROS = [
    "That's your latest briefing.",
    "That's all for now.",
]

_TRANSITIONS: dict[str, str] = {
    "politics":     "In politics...",
    "world":        "Elsewhere...",
    "business":     "In business news...",
    "sport":        "In sport...",
    "technology":   "In technology...",
    "entertainment":"In entertainment...",
    "health":       "In health news...",
    "science":      "In science...",
    "climate":      "In climate news...",
}

_DEFAULT_TRANSITION = "Next..."


@dataclass(frozen=True)
class BulletinResult:
    script: str
    segments: list[dict]


def assemble(
    stories: list[dict],
    *,
    seed: int | None = None,
) -> BulletinResult:
    """
    Assemble a bulletin from an ordered list of story dicts.

    Each story dict must contain:
      story_id:         str
      audio_script:     str   (spoken text; falls back to summary_text if empty)
      primary_category: str | None

    seed pins the intro/outro choice — pass ranking_run_id for determinism.
    """
    rng = random.Random(seed)
    segments: list[dict] = []

    # ── Intro ──────────────────────────────────────────────────────────────
    segments.append({"type": "intro", "text": rng.choice(_INTROS)})

    # ── Stories with transitions between them ──────────────────────────────
    for i, story in enumerate(stories):
        # Transition before stories 2..N, announcing the upcoming topic
        if i > 0:
            cat = (story.get("primary_category") or "").split(".")[0].lower()
            transition_text = _TRANSITIONS.get(cat, _DEFAULT_TRANSITION)
            segments.append({"type": "transition", "text": transition_text})

        story_text = (story.get("audio_script") or "").strip() or (story.get("summary_text") or "")
        segments.append({
            "type": "story",
            "story_id": story["story_id"],
            "text": story_text,
        })

    # ── Outro ──────────────────────────────────────────────────────────────
    segments.append({"type": "outro", "text": rng.choice(_OUTROS)})

    script = "\n\n".join(seg["text"] for seg in segments)
    return BulletinResult(script=script, segments=segments)
