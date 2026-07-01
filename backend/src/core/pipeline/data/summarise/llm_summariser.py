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

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 950
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


_TONE_GUIDANCE: dict[str, str] = {
    "politics":      "Dry, measured. A slight knowing edge is fine. Avoid breathlessness.",
    "world":         "Calm authority. If it's conflict or tragedy, gravity — not drama.",
    "business":      "Clear, brisk. Numbers are the story — make them land.",
    "technology":    "Measured curiosity. Not hype. Specific and grounded.",
    "sport":         "Energy. Results matter — lead with the outcome.",
    "entertainment": "Light, a little playful. Still specific.",
    "health":        "Reassuring, not alarmist. Precision over generalisation.",
    "science":       "Wonder, not jargon. Make the finding feel significant.",
    "climate":       "Serious but not catastrophising. Concrete data where possible.",
}


# Per-tier audio_script spec (depth-by-rank). Depth scales with the story's rank
# in the bulletin: the lead gets a full treatment, the tail a sentence or two.
_DEPTH_SPEC: dict[str, str] = {
    "lead":
        "  ~120 words, 4–6 sentences — the LEAD story: full treatment. Hook first "
        "sentence, develop with specific facts, forward-looking close.",
    "major":
        "  ~80 words, 3–4 sentences. Hook, two or three key facts, one line of why "
        "it matters. Tighter than the lead.",
    "standard":
        "  ~50 words, 2–3 sentences. The headline fact and its immediate significance. "
        "No padding.",
    "brief":
        "  ~25 words, 1–2 sentences. A single essential fact, briskly — 'also today…'. "
        "One crisp sentence is fine.",
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

    # Depth-by-rank: the audio_script length target for this story's tier.
    if depth_tier and depth_tier in _DEPTH_SPEC:
        depth_spec = _DEPTH_SPEC[depth_tier]
    elif depth_words:
        depth_spec = f"  ~{depth_words} words — match length to importance; concise, no padding."
    else:
        depth_spec = "  4–6 sentences, 120–180 words. Written to be heard, not read."

    return (
        "You are a senior producer at a national radio news station — the kind whose bulletins "
        "listeners actually remember. Your job: write story segments that hold attention during a commute.\n\n"
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
        "  2–3 sentences. Factual prose only.\n\n"

        "why_it_matters:\n"
        "  1–2 sentences. Name who is affected and what concrete change or decision this signals.\n"
        "  BANNED: 'ongoing concerns', 'raises questions about', 'highlights the importance of', "
        "'amid concerns', 'underscores', 'it remains to be seen', or any phrase that fits every story.\n\n"

        "audio_script:\n"
        f"{depth_spec}\n"
        "  Written to be heard, not read. Match the word target above — it scales with the\n"
        "  story's importance in this bulletin (lead deep, tail brisk).\n\n"

        "  FIRST SENTENCE — this is the bulletin hook. It must:\n"
        "    - Name something specific: a person, a number, a place, a concrete fact\n"
        "    - Create tension or curiosity — what's at stake, what's surprising, what changed\n"
        "    - Be 1 sentence only — punchy, complete, works as a standalone opener\n"
        "    - NEVER start with: 'Authorities', 'Officials', 'The government', 'Sources say',\n"
        "      'It has been', 'There has been', 'A man has', 'A woman has', or any passive opener\n\n"

        "  BODY (only if the word target allows — a brief has no body): develop with at least one\n"
        "    specific fact per two sentences. A specific fact = a name, a number, a date, a place, a\n"
        "    direct quote, a concrete consequence. Vary sentence length. Contractions required — it's,\n"
        "    they've, here's, won't. No broadcast stiffness.\n\n"

        "  CLOSE (longer treatments): forward-looking — what happens next, who's affected, the stake.\n"
        "    NOT: vague implications, rhetorical questions, or 'the story is developing'.\n\n"

        "  BANNED PHRASES (never use in audio_script):\n"
        "    'officials are considering', 'raises questions about', 'ongoing concerns',\n"
        "    'the situation remains', 'according to reports', 'sources say',\n"
        "    'many are wondering', 'in a developing story', 'more on this as it unfolds',\n"
        "    'the story continues to develop', 'it remains unclear', 'amid uncertainty'\n\n"

        "confidence:\n"
        "  0.0–1.0. How clearly the articles support one coherent story "
        "(0.9+ = clear single story, 0.7–0.9 = mostly clear, below 0.7 = conflicting or ambiguous).\n\n"

        "Fact discipline: every claim in audio_script must come from the supplied articles. "
        "No speculation. No external knowledge."
    )
