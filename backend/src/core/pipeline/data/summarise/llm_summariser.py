"""
LLM-based story summariser.

Calls Anthropic Messages API via stdlib urllib — no SDK dependency.

Configuration (via settings / environment variables):
  ANTHROPIC_API_KEY   Required. Absent → returns None (fail closed).
  SUMMARISE_MODEL     Optional. Default: settings.fallback_model.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from core.platform.config.settings import settings
from core.pipeline.data.voice import VOICE_BLOCK

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
# Headroom for the structured output: a natural spoken lead audio_script plus
# summary_text + why_it_matters. max_tokens is a ceiling — only actual output is
# billed — so a generous cap is safe.
_MAX_TOKENS = 2000
_TIMEOUT_SECONDS = 20


def _model() -> str:
    # Explicit env var overrides settings (allows per-run model swap without .env edit)
    import os
    return os.environ.get("SUMMARISE_MODEL") or settings.fallback_model


@dataclass(frozen=True)
class SummaryResult:
    headline: str
    summary_text: str
    why_it_matters: str
    audio_script: str
    confidence: float


def summarise_story(
    *,
    story_id: str,
    representative_title: str,
    articles: list[dict],
    category: str | None = None,
    depth_words: int | None = None,
    depth_tier: str | None = None,
) -> SummaryResult | None:
    """
    Generate a structured summary for a story from its article titles and snippets.

    articles:  list of {"title": ..., "snippet": ...} dicts, most recent first.
    category:  top-level taxonomy slug (e.g. "sport", "politics.uk") — used for tone guidance.
    depth_words / depth_tier: the audio_script length target (depth-by-rank). None
               → the default full treatment (~120–180 words), for back-compat.
    Returns None on API failure or parse error (fail closed).
    """
    api_key = settings.anthropic_api_key
    if not api_key:
        logger.warning("llm_summariser: ANTHROPIC_API_KEY not set — skipping summarisation")
        return None

    prompt = _build_prompt(
        representative_title, articles, category=category,
        depth_words=depth_words, depth_tier=depth_tier,
    )
    body = json.dumps({
        "model": _model(),
        "max_tokens": _MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        _API_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("llm_summariser HTTP %s for story=%s: %s", e.code, story_id, e.reason)
        return None
    except Exception as e:
        logger.warning("llm_summariser error for story=%s: %s", story_id, e)
        return None

    try:
        text_content = raw["content"][0]["text"].strip()
        if text_content.startswith("```"):
            lines = text_content.splitlines()
            text_content = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            )
        parsed = json.loads(text_content)
        confidence = float(parsed.get("confidence", 1.0))
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence {confidence!r} out of range")
        result = SummaryResult(
            headline=str(parsed["headline"]).strip(),
            summary_text=str(parsed["summary_text"]).strip(),
            why_it_matters=str(parsed.get("why_it_matters", "")).strip(),
            audio_script=str(parsed.get("audio_script", "")).strip(),
            confidence=confidence,
        )
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        logger.warning("llm_summariser malformed response for story=%s: %s", story_id, e)
        return None

    logger.info(
        "llm_summariser OK  story=%-20s  conf=%.2f  headline=%r",
        story_id, result.confidence, result.headline[:60],
    )
    return result


# A light delivery steer per top-level category — how much warmth vs gravity to carry.
# It shapes energy, never a verdict; the shared voice owns everything else.
_TONE_GUIDANCE: dict[str, str] = {
    "politics":      "Relaxed and clear. Explain the play without taking a side.",
    "world":         "Warm but careful. If it's conflict or tragedy, quiet gravity — never drama.",
    "business":      "Easy and plain. Let the key number carry it.",
    "technology":    "Curious and grounded. No hype.",
    "sport":         "A bit of energy. Lead with the result, then the story of it.",
    "entertainment": "Light, a little playful. Still specific.",
    "health":        "Warm and precise, not alarmist.",
    "science":       "Curious — make the finding land, plainly, no jargon.",
    "climate":       "Serious and concrete, never catastrophising.",
}


# Per-tier depth (depth-by-rank). The tier controls HOW FAR DOWN the story's priority list to
# go — never how vague to be (see VOICE_BLOCK "DETAIL IS THE PRODUCT": a brief story is fewer
# facts, not fuzzier ones). Every fact concrete, most important first. Word figures are a soft
# budget guide, not a target to pad or clip to — let the facts set the length. Keep roughly in
# sync with ranking.config.DEPTH_TIERS (the duration budget).
_DEPTH_SPEC: dict[str, str] = {
    "lead":
        "  The lead — the FULL picture. Go the whole way down the story's priority list: every "
        "concrete fact a curious listener would want, most important first, cause connected to "
        "effect, then the stakes. Roughly ~110 words, but let the facts set the length.",
    "major":
        "  A major story — the core plus the next tier of detail. The key facts, the mechanism or "
        "number that earns them, and the stakes. Roughly ~80 words. Fewer facts than the lead, "
        "never vaguer.",
    "standard":
        "  The essential facts — what happened, the one or two concrete details that matter most, "
        "and why it lands. Roughly ~55 words. Fewer facts than a major story, each still specific.",
    "brief":
        "  The essential core — the single most important fact stated concretely, plus the one "
        "detail that makes it land. Roughly ~35 words. Fewer facts, not fuzzier ones.",
}


def _build_prompt(
    representative_title: str,
    articles: list[dict],
    category: str | None = None,
    depth_words: int | None = None,
    depth_tier: str | None = None,
) -> str:
    article_lines = []
    for i, a in enumerate(articles[:5], 1):
        title = (a.get("title") or "").strip()
        snippet = (a.get("snippet") or "").strip()
        article_lines.append(f"Article {i}: {title}\n{snippet[:400]}")
    articles_text = "\n\n".join(article_lines)

    # Tone guidance from top-level category
    top_cat = (category or "").split(".")[0].lower()
    tone_line = _TONE_GUIDANCE.get(top_cat, "Calm, authoritative, conversational.")
    tone_block = f"Category: {top_cat or 'general'}\nTone for this story: {tone_line}"

    # Depth-by-rank: the audio_script length feel for this story's tier.
    if depth_tier and depth_tier in _DEPTH_SPEC:
        depth_spec = _DEPTH_SPEC[depth_tier]
    elif depth_words:
        depth_spec = (f"  The core concrete facts, most important first — roughly {depth_words} words, "
                      "but let the facts set the length. Fewer facts, never vaguer.")
    else:
        depth_spec = ("  The core concrete facts, most important first — roughly ~70 words, but let the "
                      "facts set the length. Fewer facts, never vaguer.")

    return (
        VOICE_BLOCK + "\n\n"
        "──────────\n"
        "YOUR TASK: write the spoken text for ONE story in the briefing. The 'audio_script' is what "
        "the listener HEARS — it must be exactly the voice above (that IS what a story sounds like "
        "here). The other three fields are short written metadata, not spoken.\n\n"
        f"Story topic: {representative_title}\n\n"
        f"{tone_block}\n\n"
        f"Source articles (most recent first):\n{articles_text}\n\n"
        "Return ONLY the following JSON — no markdown fences, no explanation:\n"
        '{"headline": "...", "summary_text": "...", "why_it_matters": "...", '
        '"audio_script": "...", "confidence": 0.0}\n\n'
        "FIELD RULES:\n\n"

        "headline:\n"
        "  Max 12 words. Active voice, present tense. No source attribution.\n\n"

        "summary_text:\n"
        "  2–3 sentences. Factual prose only (written metadata, not spoken).\n\n"

        "why_it_matters:\n"
        "  1–2 sentences. Name who is affected and what concrete change or decision this signals.\n"
        "  BANNED: 'ongoing concerns', 'raises questions about', 'highlights the importance of', "
        "'amid concerns', 'underscores', 'it remains to be seen', or any phrase that fits every story.\n\n"

        "audio_script:  ← THE SPOKEN STORY. This is the one that must be in the voice above.\n"
        f"{depth_spec}\n"
        "  Length scales with the story's rank (lead has room to breathe, the tail is quick), but "
        "every tier is that same warm spoken catch-up. Open naturally and pull the listener in — a "
        "concrete detail (a person, a number, a place), NOT a passive newsroom opener ('Authorities', "
        "'Officials', 'The government', 'Sources say', 'It has been', 'There has been', 'A man has'). "
        "Then connect the dots: cause to effect, the mechanics, why it lands for the people in it — "
        "all of it drawn from the articles below, nothing invented, no verdict on who's right.\n"
        "  BANNED PHRASES (newsroom filler that breaks the voice): 'officials are considering', "
        "'raises questions about', 'ongoing concerns', 'the situation remains', 'according to "
        "reports', 'sources say', 'many are wondering', 'in a developing story', 'more on this as it "
        "unfolds', 'the story continues to develop', 'it remains unclear', 'amid uncertainty'.\n\n"

        "confidence:\n"
        "  0.0–1.0. How clearly the articles support one coherent story "
        "(0.9+ = clear single story, 0.7–0.9 = mostly clear, below 0.7 = conflicting or ambiguous).\n\n"

        "Fact discipline: every claim in audio_script must come from the supplied articles. "
        "No speculation. No external knowledge."
    )
