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
_MAX_TOKENS = 600
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
) -> SummaryResult | None:
    """
    Generate a structured summary for a story from its article titles and snippets.

    articles: list of {"title": ..., "snippet": ...} dicts, most recent first.
    Returns None on API failure or parse error (fail closed).
    """
    api_key = settings.anthropic_api_key
    if not api_key:
        logger.warning("llm_summariser: ANTHROPIC_API_KEY not set — skipping summarisation")
        return None

    prompt = _build_prompt(representative_title, articles)
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


def _build_prompt(representative_title: str, articles: list[dict]) -> str:
    article_lines = []
    for i, a in enumerate(articles[:5], 1):
        title = (a.get("title") or "").strip()
        snippet = (a.get("snippet") or "").strip()
        article_lines.append(f"Article {i}: {title}\n{snippet[:300]}")
    articles_text = "\n\n".join(article_lines)

    return (
        "You are a senior producer at a national radio news station. "
        "Your job is to write story segments for a spoken bulletin — the kind that holds attention during a commute.\n\n"
        f"Story topic: {representative_title}\n\n"
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
        "  1–2 sentences. Be specific: name who is affected, what decision hangs on this, "
        "or what concrete change it signals.\n"
        "  Never use: 'ongoing concerns', 'raises questions about', 'highlights the importance of', "
        "'amid concerns', 'underscores', or any phrase that could fit any story.\n\n"
        "audio_script:\n"
        "  70–85 words, written to be read aloud.\n\n"
        "  Opening sentence: lead with what is striking, consequential, or unexpected — "
        "not with who announced it. "
        "Never open with 'Authorities', 'Officials say', 'A man has', 'A woman has', "
        "'The government has', 'It has been', 'There has been', or a passive-voice subject.\n\n"
        "  Sentence rhythm: vary the length. Short punchy sentences land harder next to longer ones. "
        "Avoid the same sentence structure twice in a row.\n\n"
        "  Tone: human and spoken. Contractions are fine — it's, they've, here's, there's. "
        "No 'according to'. No 'it is worth noting'. No formal broadcast stiffness.\n\n"
        "  Closing line: end on something concrete — the next development, the sharpest fact, "
        "or what the listener should watch for. Not a vague implication or rhetorical question.\n\n"
        "confidence:\n"
        "  0.0–1.0. How clearly the articles support one coherent story "
        "(0.9+ = clear, 0.7–0.9 = mostly clear, below 0.7 = conflicting or ambiguous).\n\n"
        "Fact discipline: use only information from the supplied articles above. No speculation. No external knowledge."
    )
