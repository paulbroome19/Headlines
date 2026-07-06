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
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from core.platform.config.settings import settings
from core.pipeline.data.normalise.categorise.llm_categoriser import guard_valid

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
# The review returns one object per CHANGED story (id + category + significance + top_story +
# a short reason). Over a focused front-page set that is easily a dozen+ objects; 900 tokens
# truncated the JSON mid-array → the whole parse failed → the pass silently returned nothing
# (no corrections, no top_story). 3000 gives ample headroom.
_MAX_TOKENS = 3000
_TIMEOUT_SECONDS = 25

# How far the editor may move a story's 0–10 significance. Bounds the override: it can
# reorder near-neighbours and lift/sink a clear outlier, but a 3+ point gap survives.
SIG_CLAMP = 2.0
# Safety ceiling on top_story flags per review — pure runaway protection (an over-eager LLM
# can't flood the front page), NOT a target. The user gets EVERY genuine top story; the high
# bar keeps the real count small, and duplicate event-clusters collapse in dedup downstream. So
# this sits well above a normal day's few, only clipping a pathological over-flag.
MAX_PROMOTIONS = 15


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
    category: str | None       # CORRECTED category (valid target) or None = keep. NOT used to
                               # promote to the front page any more — that is the top_story flag.
    significance_delta: float  # already clamped to ±SIG_CLAMP
    reason: str
    top_story: bool = False    # genuine front-page significance (a SEPARATE flag on top of category)


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
        "1. CATEGORY — set a corrected category when the assigned one is clearly wrong. This "
        "is what the story IS (its subject); it does NOT decide the front page.\n"
        "2. SIGNIFICANCE — an integer nudge in [-2,-1,0,1,2]: DOWN for a high-coverage but "
        "trivial story (a product reissue, a listicle) that is ranked too high; UP for a "
        "genuinely major story that is thinly covered and ranked too low. 0 for everything "
        "you don't feel strongly about (most stories).\n"
        "3. TOP STORY (\"top_story\": true) — a SEPARATE flag, ON TOP of the category (the story "
        "keeps its real category either way). Judge EACH story against the RUBRIC below on its "
        "PRINCIPLE — do not pattern-match a keyword list. Set true only when it genuinely clears "
        "the rubric.\n\n"
        "--- TOP STORY RUBRIC ---\n"
        "This is a UK national news service. Judge the front page as the editor of a UK national "
        "outlet (think the BBC News at Ten): major UK DOMESTIC news is front-page material for our "
        "audience, ON PAR with major world news — a major UK government, Parliament, NHS or public-"
        "policy story is exactly what our audience expects to lead, and must NOT be dismissed as "
        "'routine' just because it is domestic rather than international or dramatic.\n\n"
        "CORE TEST: Would this story matter to the average person in the UK regardless of their "
        "personal interests? A top story is something you'd expect to lead a UK national broadcast "
        "— major UK news, OR major world news with broad public consequence. The DEFAULT is NOT a "
        "top story. Most news, even important-to-someone news, is not. Only the day's genuinely "
        "significant few qualify — typically a small handful (aim ~3-6), never a dozen; on a normal "
        "day EXPECT a major UK story or two among them.\n\n"
        "A story qualifies ONE of two ways:\n\n"
        "ROUTE 1 — BROAD HUMAN IMPACT. It affects, endangers, or genuinely matters to the "
        "general population:\n"
        "- war and armed conflict\n"
        "- natural disasters (earthquakes, floods, wildfires, major storms)\n"
        "- major disease outbreaks / public-health emergencies\n"
        "- significant UK government & politics: the government, Parliament, Downing Street, "
        "elections, a cabinet minister resigning or being sacked, major legislation or a budget, "
        "a government crisis or U-turn, or broad-impact domestic policy — anything materially "
        "shaping the country. (For a UK audience this is core front-page news, not 'routine.')\n"
        "- the NHS and major public services: strikes / industrial action, major reforms, funding "
        "crises, or serious service failures that affect the public\n"
        "- major international political & government events (foreign elections, resignations, "
        "government collapse) with broad consequence\n"
        "- major scandals/controversies involving powerful people or institutions\n"
        "- serious and significant crime; terrorism\n"
        "- landmark court outcomes / major prison sentences\n"
        "- large-scale accidents and tragedies\n"
        "- economic events that hit ordinary people directly (a crash, a major rates decision "
        "affecting mortgages — NOT a single company's internal news)\n\n"
        "ROUTE 2 — EXCEPTIONAL WITHIN ITS CATEGORY. A story so significant within its own field "
        "that it transcends that field and becomes national conversation, even if the category is "
        "normally \"soft.\" England winning the World Cup is a national event, not sport news. The "
        "bar is HIGH: people who don't follow that category are still talking about it. A normal "
        "match result, ordinary product launch, or routine awards show does NOT clear it.\n\n"
        "DOES NOT QUALIFY (with reasoning):\n"
        "- Corporate/business news — layoffs, earnings, deals, company strategy. Microsoft layoffs "
        "matter to an industry, not the general population. Business news.\n"
        "- Tech/product news — launches, updates, roundups. Sony's disc policy is a tech story, "
        "not a national event.\n"
        "- Routine sport — results, transfers, standings — unless Route 2 exceptional.\n"
        "- Celebrity/entertainment — weddings, gossip, casting — unless a genuinely major figure "
        "AND major event.\n"
        "- TRULY routine politics — a minor committee session, a junior or local appointment, a "
        "small procedural vote, incremental tweaks with no material public impact. But do NOT lump "
        "SIGNIFICANT UK government / NHS / major-policy stories in here — those qualify via Route 1. "
        "When genuinely unsure on a MAJOR UK domestic story, lean toward flagging it.\n"
        "--- END RUBRIC ---\n\n"
        f"VALID category slugs (a corrected category MUST be one of these leaves, or a "
        f"top-level bucket {', '.join(tops)}):\n" + "\n".join(leaves) + "\n\n"
        "RULES:\n"
        "- Return ONLY the stories you are changing (a category fix, a significance nudge, or a "
        "top_story flag). Omit stories you leave alone.\n"
        "- Judge by the CONTENT (headline + snippet), not the coverage number.\n"
        "- Be sparing with top_story — a handful at most on a normal day, zero is fine.\n\n"
        "Respond with JSON only:\n"
        '{"adjustments": [{"id": "<story_id>", "category": "<valid slug or omit>", '
        '"significance": <-2..2>, "top_story": <true|false>, "reason": "<6 words max>"}, ...]}'
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
        # Editorial judgment is a classification, not creative writing — temperature 0 makes it
        # DETERMINISTIC (the API default 1.0 made the same candidate set flag differently each run).
        "temperature": 0,
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


def _parse_items(text: str) -> list[dict]:
    """Extract the adjustment objects, tolerating a slightly-truncated response. Tries the
    clean `{"adjustments":[...]}` parse first; on failure, salvages every COMPLETE flat
    `{...}` object with an `id` (a truncated final object is simply dropped) so an over-length
    response still yields the rows it did finish."""
    start = text.find("{")
    if start >= 0:
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(payload, dict) and isinstance(payload.get("adjustments"), list):
                return payload["adjustments"]
        except (ValueError, json.JSONDecodeError):
            pass
    # Salvage — adjustment objects are FLAT (no nested braces), so a shortest-match {...} scan
    # recovers each complete one and skips a cut-off tail.
    out: list[dict] = []
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            o = json.loads(m.group())
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(o, dict) and "id" in o:
            out.append(o)
    if out:
        logger.info("editorial: salvaged %d adjustment(s) from a truncated response", len(out))
    return out


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
    except (KeyError, IndexError, TypeError) as e:
        logger.warning("editorial malformed response: %s", e)
        return {}
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("```"))
    items = _parse_items(text)

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
        top = bool(it.get("top_story", False))
        if cat is None and sig == 0.0 and not top:
            continue  # nothing actionable
        out[sid] = EditorialAdjustment(
            story_id=sid, category=cat, significance_delta=sig,
            reason=str(it.get("reason", "")).strip(), top_story=top,
        )
    return out
