"""
Lead-story OPENER split — decouples first-play latency from the lead's full length.

The lead story is the deepest tier (~260 words ≈ ~100s of audio), so gating playback on
its ENTIRE audio makes first-play swing with lead length (~30s). Instead we split the lead
into a short natural OPENER (first whole sentence(s), sized so its audio covers ~20-25s) and
a REMAINDER. Both are `story` segments with the SAME story_id, played back-to-back; the iOS
client groups consecutive same-story_id segments into one story unit. safe_to_start then
gates on intro + opener (~15-18s, consistent) while the remainder streams in behind it.

Pure + no IO so it's unit-testable and shared by the assembler and the skeleton.
"""
from __future__ import annotations

import re

# ~20-25s of speech at the measured ~156 wpm ElevenLabs rate. The opener accumulates WHOLE
# sentences until it crosses this, so it lands a little above (~22-27s) — a natural line, never
# a chopped fragment. Tunable.
OPENER_TARGET_WORDS = 50

# Sentence boundary: end punctuation followed by whitespace. Good enough for broadcast prose
# (the summaries avoid abbreviations like "U.S." in spoken text — numbers/acronyms are already
# expanded by pronunciation.normalise_for_tts downstream).
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def split_lead_opener(text: str, target_words: int = OPENER_TARGET_WORDS) -> tuple[str, str]:
    """Split spoken lead text into (opener, remainder) on a CLEAN sentence boundary.

    The opener accumulates whole sentences until it reaches ~target_words, always leaving at
    least one sentence for the remainder — so it reads as a natural opening line, never a
    chopped fragment. Returns (text, "") only when the text is a single word (never a real
    story), signalling "do not split".
    """
    text = (text or "").strip()
    words = text.split()
    if len(words) < 2:
        return text, ""

    sentences = [s for s in _SENTENCE.split(text) if s.strip()]
    if len(sentences) >= 2:
        wc = 0
        last = 0
        for last, s in enumerate(sentences):
            wc += len(s.split())
            if wc >= target_words and last < len(sentences) - 1:
                break
        cut = min(last + 1, len(sentences) - 1)  # ≥1 sentence stays in the remainder
        return " ".join(sentences[:cut]).strip(), " ".join(sentences[cut:]).strip()

    # A single long sentence (rare for a 260-word lead): fall back to a word-count cut so the
    # opener still exists. Not a sentence boundary, but only reachable for pathological input.
    cut = min(target_words, len(words) - 1)
    return " ".join(words[:cut]).strip(), " ".join(words[cut:]).strip()


def apply_lead_opener(segments: list[dict]) -> list[dict]:
    """Split the FIRST story segment into two consecutive story segments (opener + remainder)
    carrying the same story_id, tagged with `lead_part`. Returns a NEW list; leaves the input
    unchanged if there is no story segment or the lead is unsplittable. Idempotent: a segment
    already tagged `lead_part` is left alone.
    """
    for i, seg in enumerate(segments):
        if seg.get("type") != "story":
            continue
        if seg.get("lead_part"):
            return segments  # already split
        opener, remainder = split_lead_opener(seg.get("text") or "")
        if not remainder:
            return segments  # unsplittable → keep the single lead segment
        opener_seg = {**seg, "text": opener, "lead_part": "opener"}
        remainder_seg = {**seg, "text": remainder, "lead_part": "remainder"}
        return segments[:i] + [opener_seg, remainder_seg] + segments[i + 1:]
    return segments
