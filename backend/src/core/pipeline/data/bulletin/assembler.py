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

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone

from core.pipeline.data.bulletin.intros import build_intro
from core.pipeline.data.bulletin.transitions import pick_transition
from core.pipeline.data.bulletin.outros import build_outro
from core.pipeline.data.bulletin.pronunciation import normalise_for_tts
from core.pipeline.data.bulletin.connective import ConnectiveResult
from core.pipeline.data.bulletin.opener import apply_lead_opener

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BulletinResult:
    script: str
    segments: list[dict]


def template_bridges(stories: list[dict], *, seed: int | None = None) -> dict[str, str]:
    """Template (non-LLM) transition text for every NON-lead story, keyed by story_id — the exact
    pick_transition path assemble()'s template branch uses. The streaming play path uses this as the
    fallback when the LLM bridge pass (generate_bridges_and_outro) fails, so a briefing is NEVER left
    with SILENT bridges (the failure that stripped connective tissue from full-size briefings). The
    lead (index 0) takes no bridge — the greeting leads straight into it — so it is omitted."""
    rng = random.Random(seed)
    used: set[str] = set()
    out: dict[str, str] = {}
    for i, story in enumerate(stories):
        if i == 0:
            continue
        text = pick_transition(
            rng, previous_story=stories[i - 1], next_story=story,
            position=i - 1, total_stories=len(stories), used_in_bulletin=used,
        )
        used.add(text)
        out[str(story["story_id"])] = text
    return out


def assemble(
    stories: list[dict],
    *,
    seed: int | None = None,
    name: str | None = None,
    now: datetime | None = None,
    is_first_bulletin: bool = False,
    connective: ConnectiveResult | None = None,
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
    connective        — pre-generated LLM connective tissue; when provided the
                        template intro/transitions/outro are bypassed. Pass None
                        to use the fallback template path (unchanged behaviour).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    rng = random.Random(seed)
    segments: list[dict] = []

    if connective is not None:
        # ── Defensive check: order must be a permutation of our story ids ─────
        by_id = {str(s["story_id"]): s for s in stories}
        if set(connective.order) != set(by_id):
            logger.warning(
                "assemble: connective.order %r does not match story ids %r — "
                "falling back to templates",
                connective.order, list(by_id),
            )
            connective = None

    if connective is not None:
        # ── LLM connective path ────────────────────────────────────────────────
        # Reorder stories using the model's chosen narrative order so that
        # each transition[id] actually previews the correct next story.
        ordered = [by_id[sid] for sid in connective.order]
        segments.append({"type": "intro", "text": connective.greeting})

        for i, story in enumerate(ordered):
            if i > 0:
                sid = str(story["story_id"])
                # Bind the bridge to the PAIR it was written for, not to a position. A transition
                # previews `next_story_id` and follows `prev_story_id`; if a play-time reorder/dedup
                # ever separates it from that exact pair, it must be dropped (clean cut) or
                # regenerated — never played naming the wrong story. See bridge_guard.
                segments.append({
                    "type": "transition",
                    "text": connective.transitions.get(sid, ""),
                    "prev_story_id": str(ordered[i - 1]["story_id"]),
                    "next_story_id": sid,
                })

            story_text = (story.get("audio_script") or "").strip() or (story.get("summary_text") or "")
            segments.append({
                "type": "story",
                "story_id": story["story_id"],
                "text": story_text,
            })

        segments.append({"type": "outro", "text": connective.outro})

    else:
        # ── Template fallback path (unchanged) ────────────────────────────────
        intro_text = build_intro(
            rng,
            name=name,
            now=now,
            stories=stories,
            is_first_bulletin=is_first_bulletin,
        )
        segments.append({"type": "intro", "text": intro_text})

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
                segments.append({
                    "type": "transition",
                    "text": transition_text,
                    "prev_story_id": str(stories[i - 1]["story_id"]),
                    "next_story_id": str(story["story_id"]),
                })

            story_text = (story.get("audio_script") or "").strip() or (story.get("summary_text") or "")
            segments.append({
                "type": "story",
                "story_id": story["story_id"],
                "text": story_text,
            })

        outro_text = build_outro(
            rng,
            name=name,
            now=now,
            story_count=len(stories),
        )
        segments.append({"type": "outro", "text": outro_text})

    # ── Lead OPENER split ───────────────────────────────────────────────────────
    # Split the lead story into a short opener + remainder (two story segments, same
    # story_id) BEFORE normalisation/script so hashes, script and _story_timings all stay
    # consistent. safe_to_start then gates on intro + opener (~20-25s) rather than the whole
    # ~100s lead. iOS groups the two segments into one story unit. No-op for a short lead.
    segments = apply_lead_opener(segments)

    # ── Assemble script + pronunciation normalisation ──────────────────────────
    # Normalise each segment's text for TTS, then derive the script from the
    # normalised segments. The per-segment audio path (segment_synth) hashes and
    # synthesises seg["text"], so normalising here is what actually reaches
    # ElevenLabs. Joining already-normalised segments is equivalent to normalising
    # the joined string (no normalise pattern spans the "\n\n" boundary) and avoids
    # double-normalisation — and keeps script, segments, hashes, and the
    # _story_timings offset math all consistent (len(script) == Σ(len(seg)+2)).
    for seg in segments:
        seg["text"] = normalise_for_tts(seg["text"])
    script = "\n\n".join(seg["text"] for seg in segments if seg["text"])

    return BulletinResult(script=script, segments=segments)
