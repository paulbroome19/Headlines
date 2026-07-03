"""
LLM EDITORIAL PASS — the final-boss review over categorisation + ranking.

Same spirit as the connective-tissue LLM, but it runs on the RANKED candidate set (after
cluster → categorise[#113] → rank, before selection): an editor looking at the front page
before it goes out. In ONE cheap Haiku call it reviews the top contenders and returns
BOUNDED corrections only — it never re-ranks from scratch:

  • CATEGORY — fix a clearly-wrong category (belt-and-braces over the #113 categoriser).
  • SIGNIFICANCE — nudge a story's importance UP or DOWN by at most ±SIG_CLAMP where the
    mechanical coverage rank is obviously wrong (a 27-source PlayStation-disc reissue
    pulled down; a genuinely major but thinly-covered story pushed up). The clamp means
    it adjusts, it cannot override the pipeline.
  • TOP-STORY — promote a story to the front page (category → top-stories.<region>) when
    it's a major overlap of the topic categories OR significant news with no topic home
    (a major disaster/crime/accident #113 left uncategorised); demote a trivial one off it.

Guardrails: significance clamped to ±SIG_CLAMP; category constrained to the SAME valid
targets as #113 (guard_valid); net new front-page promotions capped (MAX_PROMOTIONS).
Only stories the editor actually changes are returned. On any failure the candidate set is
used unchanged (the pass is purely additive).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from core.platform.config.settings import settings
from core.pipeline.data.normalise.categorise.llm_categoriser import guard_valid

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 900
_TIMEOUT_SECONDS = 25

# How far the editor may move a story's 0–10 significance. Bounds the override: it can
# reorder near-neighbours and lift/sink a clear outlier, but a 3+ point gap survives.
SIG_CLAMP = 2.0
# Cap on NET new front-page promotions per review — stops a wholesale reshaping of the
# front page; beyond this, the highest-significance promotions win.
MAX_PROMOTIONS = 6


@dataclass(frozen=True)
class StoryMeta:
    story_id: str
    headline: str
    snippet: str
    category: str | None
    sources: int
    rank: int          # 1-based position in the current ranking


@dataclass(frozen=True)
class EditorialAdjustment:
    story_id: str
    category: str | None       # corrected/promoted category (valid target) or None = keep
    significance_delta: float  # already clamped to ±SIG_CLAMP
    reason: str


def _model() -> str:
    return settings.fallback_model  # Haiku


def build_system(valid_leaves: list[str]) -> str:
    leaves = sorted(valid_leaves)
    tops = sorted({leaf.split(".")[0] for leaf in leaves})
    return (
        "You are the DUTY EDITOR reviewing the front page before it goes out. The stories "
        "below were categorised and ranked by an automated pipeline (rank is by coverage "
        "volume). Your job is NOT to re-rank from scratch — trust the pipeline — but to fix "
        "CLEAR mistakes only. Change as little as possible.\n\n"
        "You may, PER STORY, do any of:\n"
        "1. CATEGORY — set a corrected category when the assigned one is clearly wrong.\n"
        "2. SIGNIFICANCE — an integer nudge in [-2,-1,0,1,2]: DOWN for a high-coverage but "
        "trivial story (a product reissue, a listicle) that is ranked too high; UP for a "
        "genuinely major story that is thinly covered and ranked too low. 0 for everything "
        "you don't feel strongly about (most stories).\n"
        "3. TOP STORY — set category to top-stories.<region> (uk/us/europe/middle-east/"
        "africa/asia) to promote a story to the front page. A story is a top story ONLY if "
        "it is a major overlap of the topic categories (big politics/business/world) OR "
        "significant hard news with no topic home (a major disaster, major crime, major "
        "accident). Top-stories is a SIGNIFICANCE judgment, NOT a catch-all bin — a trivial "
        "or narrow story is never a top story; demote one that is wrongly there by giving it "
        "its real topic category.\n\n"
        f"VALID category slugs (a corrected category MUST be one of these leaves, or a "
        f"top-level bucket {', '.join(tops)}):\n" + "\n".join(leaves) + "\n\n"
        "RULES:\n"
        "- Return ONLY the stories you are changing. Omit stories you leave alone.\n"
        "- Judge by the CONTENT (headline + snippet), not the coverage number.\n"
        "- Do not move more than a handful of stories; you are correcting, not rewriting.\n\n"
        "Respond with JSON only:\n"
        '{"adjustments": [{"id": "<story_id>", "category": "<valid slug or omit>", '
        '"significance": <-2..2>, "reason": "<6 words max>"}, ...]}'
    )


def build_user(stories: list[StoryMeta]) -> str:
    lines = []
    for s in stories:
        cat = s.category or "UNCATEGORISED"
        snip = (s.snippet or "")[:180]
        lines.append(
            f'#{s.rank} id={s.story_id} [{cat}] sources={s.sources}\n'
            f'   "{s.headline}"' + (f"\n   {snip}" if snip else "")
        )
    return "FRONT-PAGE CANDIDATES (rank = current order):\n\n" + "\n".join(lines)


def _post_anthropic(system: str, user: str) -> dict | None:
    api_key = settings.anthropic_api_key
    if not api_key:
        return None
    data = json.dumps({
        "model": _model(),
        "max_tokens": _MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        _API_URL, data=data,
        headers={"x-api-key": api_key, "anthropic-version": _API_VERSION,
                 "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("editorial HTTP %s: %s", e.code, e.reason)
    except Exception as e:
        logger.warning("editorial error: %s", e)
    return None


def _clamp(x: float) -> float:
    return max(-SIG_CLAMP, min(SIG_CLAMP, x))


def review_front_page(
    stories: list[StoryMeta], valid_leaves: list[str],
) -> dict[str, EditorialAdjustment]:
    """One Haiku call reviewing the ranked candidates. Returns story_id → bounded
    adjustment for ONLY the stories the editor changed. Empty dict on any failure."""
    if not stories:
        return {}
    raw = _post_anthropic(build_system(valid_leaves), build_user(stories))
    if raw is None:
        return {}
    try:
        text = raw["content"][0]["text"].strip()
        if text.startswith("```"):
            text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("```"))
        start = text.find("{")
        payload, _ = json.JSONDecoder().raw_decode(text[start:]) if start >= 0 else ({}, 0)
        items = payload.get("adjustments") or []
    except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError) as e:
        logger.warning("editorial malformed response: %s", e)
        return {}

    known = {s.story_id for s in stories}
    out: dict[str, EditorialAdjustment] = {}
    for it in items:
        try:
            sid = str(it["id"]).strip()
        except (KeyError, TypeError):
            continue
        if sid not in known:
            continue
        # category: keep only a valid target (belt-and-braces — never persist junk).
        cat = it.get("category")
        cat = guard_valid(str(cat).strip(), valid_leaves) if cat else None
        try:
            sig = _clamp(float(it.get("significance", 0)))
        except (ValueError, TypeError):
            sig = 0.0
        if cat is None and sig == 0.0:
            continue  # nothing actionable
        out[sid] = EditorialAdjustment(
            story_id=sid, category=cat, significance_delta=sig,
            reason=str(it.get("reason", "")).strip(),
        )
    return out
