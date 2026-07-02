"""
LLM-PRIMARY cluster-level categoriser (docs/categorisation-llm-primary.md).

Replaces the context-blind keyword matcher as the PRIMARY classifier. One Haiku call
per STORY (cluster), constrained STRICTLY to current taxonomy leaves, with the
ingestion pool topic + country supplied as *hints, not deciders*. This fixes the
diagnosed failures:

  • verb/noun keyword collisions  ("No 10 SHARES shock…" → business.markets)
  • bare place-name entity boosts  ("Pharmacies in Leicester" → Leicester City FC)
  • region-blind politics          (Modi/India & Walz/US both → politics.uk)
  • pool blindly overriding content (a business-pool story forced to business)

Design:
  - STRICT constraint — the model must return a slug copied from the valid-leaf list;
    anything else is rejected (never persisted). The list is the single source of truth.
  - HINTS, not deciders — pool topic/country are given as weak context; the CONTENT
    decides. Politics becomes region-aware to the extent the taxonomy allows (a country
    without its own politics leaf maps to the closest one, e.g. India → politics.world).
  - CACHEABLE — pure function of (title, snippets, hints, taxonomy_version); callers
    cache by content hash so a story is classified at most once (see categorisation_repo).

The Anthropic call reuses the stdlib-urllib pattern from llm_fallback (no SDK dep).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from core.platform.config.settings import settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 200
_TIMEOUT_SECONDS = 20

# A story must clear this to override to a confident primary. Below it we still return
# the model's best guess (callers may keep it but flag low-confidence); the valid-slug
# guard is separate and absolute.
MIN_CONFIDENCE = 0.55


@dataclass(frozen=True)
class StoryCategorisation:
    primary: str
    secondaries: list[str]
    confidence: float
    reason: str
    method: str = "llm-primary"
    # Not part of equality — diagnostic only.
    raw_slug: str | None = field(default=None, compare=False)


def _ancestor_nodes(leaves: list[str]) -> set[str]:
    """Every valid node in the taxonomy tree = all ancestor prefixes of every leaf
    (e.g. 'sport.football.premier-league.arsenal' → sport, sport.football, …). Used to
    reroute a plausible-but-nonexistent slug up to the nearest node that DOES exist."""
    nodes: set[str] = set()
    for leaf in leaves:
        parts = leaf.split(".")
        for i in range(1, len(parts) + 1):
            nodes.add(".".join(parts[:i]))
    return nodes


def guard_and_reroute(primary: str, valid_leaves: set[str], valid_nodes: set[str]) -> str | None:
    """VALID-SLUG GUARD: nothing invalid ever persists. A valid leaf passes through. A
    plausible-but-nonexistent slug (taxonomy gap: 'sport.nhl', 'top-stories.north-america')
    is REROUTED to its nearest existing ancestor node ('sport', 'top-stories'). Anything
    with no valid ancestor is REJECTED (None)."""
    if primary in valid_leaves:
        return primary
    parts = primary.split(".")
    while len(parts) > 1:
        parts = parts[:-1]
        cand = ".".join(parts)
        if cand in valid_nodes:
            return cand
    return None


def _model() -> str:
    return settings.fallback_model  # Haiku (claude-haiku-4-5-…)


def build_system(valid_leaves: list[str]) -> str:
    """The STATIC instruction + valid-leaf catalog. Identical for every story in a run,
    so it is sent as a cache_control'd system block — Anthropic prompt-caches it and each
    subsequent story pays ~0.1x for this (much larger) half of the prompt (see cost model)."""
    catalog = "\n".join(sorted(valid_leaves))
    return (
        "You are a strict news categoriser. Assign ONE primary category to each story, "
        "copied EXACTLY from the allowed list. Judge by the STORY CONTENT — the pool "
        "hints are weak context that is often wrong; never let them override the content.\n\n"
        f"ALLOWED CATEGORY SLUGS (choose primary from exactly these):\n{catalog}\n\n"
        "RULES:\n"
        "- primary MUST be copied verbatim from the allowed list. Never invent or "
        "abbreviate a slug, never use a legacy slug (no world.*, entertainment.*, "
        "climate, politics.global).\n"
        "- Judge the actual subject. A word like 'shares' (to share), 'summit', or a "
        "city name that is also a football club must NOT drive the category unless the "
        "story is genuinely about that topic.\n"
        "- POLITICS is region-aware: pick politics.uk / politics.us / politics.europe by "
        "the country the politics concerns; for anywhere else (India, Africa, Asia, "
        "Middle East, global) use politics.world.\n"
        "- Prefer the most specific leaf that clearly fits; otherwise use the parent-ish "
        "leaf. If genuinely unsure between topics, pick the single best and lower confidence.\n"
        "- secondary: 0-2 other allowed slugs that also apply (e.g. a crime story about a "
        "footballer). Omit if none.\n\n"
        "Respond with JSON only:\n"
        '{"primary": "<allowed slug>", "secondary": ["<allowed slug>", ...], '
        '"confidence": <0.0-1.0>, "reason": "<6 words max>"}'
    )


def build_user(*, title: str, snippets: list[str], pool_topic: str | None,
               pool_country: str | None) -> str:
    """The DYNAMIC per-story half (title + context + hints)."""
    body = " ".join(s.strip() for s in snippets if s and s.strip())[:700]
    hint_lines = []
    if pool_topic:
        hint_lines.append(f"- pool topic (weak hint, may be wrong): {pool_topic}")
    if pool_country:
        hint_lines.append(f"- source country (weak hint for region): {pool_country}")
    hints = ("\n".join(hint_lines)) or "- (none)"
    return (
        f"STORY:\nTitle: {title}\n"
        + (f"Context: {body}\n" if body else "")
        + "\nHINTS (context only, may be wrong):\n" + hints
    )


def _post_anthropic(system: str, user: str) -> dict | None:
    api_key = settings.anthropic_api_key
    if not api_key:
        return None
    data = json.dumps({
        "model": _model(),
        "max_tokens": _MAX_TOKENS,
        # Cache the large static catalog/rules; only the small per-story block is fresh.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        _API_URL, data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("llm_categoriser HTTP %s: %s", e.code, e.reason)
    except Exception as e:  # network/timeout/etc — caller falls back
        logger.warning("llm_categoriser error: %s", e)
    return None


def _parse(raw: dict, valid: set[str]) -> tuple[str, list[str], float, str] | None:
    try:
        text = raw["content"][0]["text"].strip()
        if text.startswith("```"):
            text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("```"))
        # Tolerate prose AFTER the JSON object: decode the first JSON value and ignore
        # any trailing text (the model sometimes adds an explanation line).
        start = text.find("{")
        if start == -1:
            raise ValueError("no JSON object in response")
        p, _ = json.JSONDecoder().raw_decode(text[start:])
        primary = str(p["primary"]).strip()
        secondary = [str(s).strip() for s in (p.get("secondary") or [])]
        confidence = float(p.get("confidence", 0.0))
        reason = str(p.get("reason", "")).strip()
    except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError) as e:
        logger.warning("llm_categoriser malformed response: %s", e)
        return None
    return primary, secondary, confidence, reason


def classify_story(
    *,
    title: str,
    snippets: list[str],
    pool_topic: str | None,
    pool_country: str | None,
    valid_leaves: list[str],
) -> StoryCategorisation | None:
    """Classify one story. Returns None on API failure / unusable output (caller decides
    the fallback). The returned primary is GUARANTEED to be a valid leaf."""
    valid = set(valid_leaves)
    system = build_system(valid_leaves)
    user = build_user(title=title, snippets=snippets, pool_topic=pool_topic,
                      pool_country=pool_country)
    raw = _post_anthropic(system, user)
    if raw is None:
        return None
    parsed = _parse(raw, valid)
    if parsed is None:
        return None
    raw_primary, secondary, confidence, reason = parsed

    # ── VALID-SLUG GUARD (absolute) — reroute gaps, reject the un-rerouteable ─────
    nodes = _ancestor_nodes(valid_leaves)
    primary = guard_and_reroute(raw_primary, valid, nodes)
    if primary is None:
        logger.warning("llm_categoriser rejected un-rerouteable primary %r for %r", raw_primary, title[:60])
        return None
    secondary = [s for s in secondary if s in valid and s != primary][:2]

    return StoryCategorisation(
        primary=primary,
        secondaries=secondary,
        confidence=confidence,
        reason=reason,
        method="llm-primary" if primary == raw_primary else "llm-primary-rerouted",
        raw_slug=raw_primary,
    )
