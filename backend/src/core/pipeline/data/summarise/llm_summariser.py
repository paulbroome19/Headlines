"""
LLM-based story summariser.

Calls Anthropic Messages API via stdlib urllib — no SDK dependency.

Configuration (environment variables):
  ANTHROPIC_API_KEY   Required. Absent → returns None (fail closed).
  SUMMARISE_MODEL     Optional. Default: claude-haiku-4-5-20251001.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 512
_TIMEOUT_SECONDS = 20


def _model() -> str:
    return os.environ.get("SUMMARISE_MODEL", "claude-haiku-4-5-20251001")


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
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
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
        "You are a news editor preparing a spoken radio bulletin.\n\n"
        f"Story topic: {representative_title}\n\n"
        f"Source articles (most recent first):\n{articles_text}\n\n"
        "Return ONLY the following JSON object — no markdown fences, no explanation:\n"
        '{"headline": "...", "summary_text": "...", "why_it_matters": "...", '
        '"audio_script": "...", "confidence": 0.0}\n\n'
        "Rules:\n"
        "- Summarise the story as a whole, not each article individually\n"
        "- Base every fact only on the supplied article text above\n"
        "- Do not add external knowledge or speculation\n"
        "- If articles conflict or contradict, acknowledge the uncertainty neutrally "
        "(e.g. 'reports vary on...')\n"
        "- headline: max 12 words, present tense, no source names\n"
        "- summary_text: 2-3 sentences, factual only\n"
        "- why_it_matters: 1-2 sentences on wider significance, grounded in the articles\n"
        "- audio_script: ~75 words, natural spoken language for broadcast radio — "
        "no article-style prose, no 'according to', write as if spoken aloud\n"
        "- confidence: 0.0-1.0 — how clearly the articles support one coherent story "
        "(0.9+=clear, 0.7-0.9=mostly clear, below 0.7=conflicting or ambiguous)"
    )
