"""
LLM-based fallback categoriser for articles that YAML keyword/entity matching
could not classify.

Called only when the YAML pass returns score=0 across all rules.
Calls the Anthropic Messages API directly via stdlib urllib — no SDK dependency.

Configuration (environment variables):
  ANTHROPIC_API_KEY   Required. Absent → fallback silently disabled (no crash).
  FALLBACK_MODEL      Optional. Default: claude-haiku-4-5-20251001.
                      Swap the model without touching code.

The caller decides whether to persist based on result.accepted
(confidence >= FALLBACK_CONFIDENCE_THRESHOLD).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Only persist fallback decisions at or above this confidence level.
# 0.85 is conservative: the classifier must be highly confident before we
# override the YAML "no match" result.
FALLBACK_CONFIDENCE_THRESHOLD = 0.85

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 128
_TIMEOUT_SECONDS = 15


def _model() -> str:
    """Return the model name from env, with a sensible default."""
    return os.environ.get("FALLBACK_MODEL", "claude-haiku-4-5-20251001")


@dataclass(frozen=True)
class FallbackResult:
    slug: str
    confidence: float
    reason: str

    @property
    def accepted(self) -> bool:
        return self.confidence >= FALLBACK_CONFIDENCE_THRESHOLD


def _build_prompt(title: str, snippet: str | None, valid_slugs: list[str]) -> str:
    slugs_csv = ", ".join(sorted(valid_slugs))
    snippet_line = f"\nSnippet: {snippet[:500]}" if snippet else ""
    return (
        f"Classify this news article into exactly one category slug.\n\n"
        f"Title: {title}{snippet_line}\n\n"
        f"Valid slugs: {slugs_csv}\n\n"
        f"Respond with JSON only:\n"
        f'{{"slug": "<one of the valid slugs>", "confidence": <0.0-1.0>, "reason": "<5 words max>"}}\n\n'
        f"Rules:\n"
        f"- slug must be copied exactly from the valid list above\n"
        f"- confidence: 0.9+=certain, 0.75-0.9=likely, below 0.75=uncertain\n"
        f"- Set confidence below 0.75 if the article could fit multiple categories equally\n"
        f"- reason: 5 words max — the key signal that led to your choice"
    )


def classify_fallback(
    title: str,
    snippet: str | None,
    valid_slugs: list[str],
    *,
    article_id: int | None = None,
) -> FallbackResult | None:
    """
    Ask the configured LLM to classify an article that YAML could not categorise.

    article_id is optional context for log messages; it does not affect classification.
    Returns a FallbackResult (always, for logging) or None on API failure.
    The caller checks result.accepted to decide whether to persist.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt = _build_prompt(title, snippet, valid_slugs)
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
        logger.warning("llm_fallback HTTP %s for %r: %s", e.code, title[:60], e.reason)
        return None
    except Exception as e:
        logger.warning("llm_fallback error for %r: %s", title[:60], e)
        return None

    try:
        text = raw["content"][0]["text"].strip()
        # Strip markdown code fence if model wraps in ```json ... ```
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            )
        parsed = json.loads(text)
        slug = str(parsed["slug"]).strip()
        confidence = float(parsed["confidence"])
        reason = str(parsed.get("reason", "")).strip()
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence {confidence!r} outside [0.0, 1.0]")
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        logger.warning("llm_fallback malformed response for %r: %s", title[:60], e)
        return None

    if slug not in valid_slugs:
        logger.warning(
            "llm_fallback returned unknown slug %r for %r — discarding",
            slug, title[:60],
        )
        return None

    result = FallbackResult(slug=slug, confidence=confidence, reason=reason)
    id_tag = f"id={article_id}  " if article_id is not None else ""

    if result.accepted:
        logger.info(
            "llm_fallback ACCEPTED  %sslug=%-38s conf=%.2f  reason=%r  title=%r",
            id_tag, slug, confidence, reason, title[:60],
        )
    else:
        logger.info(
            "llm_fallback REJECTED  %sslug=%-38s conf=%.2f  reason=%r  title=%r",
            id_tag, slug, confidence, reason, title[:60],
        )

    return result
