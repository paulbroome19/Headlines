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


# Sentinel the model returns when a story has no honest home in our taxonomy.
DROP = "none"

# Distinct "exclude this story" result — NOT an API failure. Callers must treat it as a
# deliberate decision to omit the story, never as a reason to fall back to another path.
DROPPED = StoryCategorisation(
    primary=DROP, secondaries=[], confidence=0.0, reason="no taxonomy fit", method="llm-drop",
)


def _ancestor_nodes(leaves: list[str]) -> set[str]:
    """Every node in the taxonomy tree = all ancestor prefixes of every leaf
    (e.g. 'sport.football.premier-league.arsenal' → sport, sport.football, …). Used by the
    backfill to spot stories whose stored primary isn't a current node at all."""
    nodes: set[str] = set()
    for leaf in leaves:
        parts = leaf.split(".")
        for i in range(1, len(parts) + 1):
            nodes.add(".".join(parts[:i]))
    return nodes


def offered_targets(leaves: list[str]) -> set[str]:
    """What a primary may legitimately be: a real leaf we offer, OR a top-level bucket we
    offer as a whole (business, sport, politics, …) for a genuinely-general story of that
    area. NOTHING ELSE — we do NOT accept a coarse ancestor as a dumping ground for an
    off-taxonomy subject."""
    tops = {leaf.split(".")[0] for leaf in leaves}
    return set(leaves) | tops


def _snap_to_leaf(slug: str, leaves: set[str]) -> str | None:
    """Snap a plausible-but-invalid slug to a REAL LEAF when it's an obvious near-miss —
    NEVER to a coarse ancestor. Handles the model's common slips:
      • a taxonomy ALIAS — a single-league sport's generic name (sport.basketball → sport.nba),
      • plural/singular drift on the last segment (sport.football.international →
        sport.football.internationals),
      • a spurious intermediate segment (sport.basketball.nba → sport.nba),
      • a spurious child on a leaf that has none (health.pediatrics → health).
    Returns the leaf, or None if nothing normalises to a real leaf (then we drop)."""
    from .category_loader import load_category_aliases
    aliased = load_category_aliases().get(slug)
    if aliased and aliased in leaves:      # semantic single-league alias (taxonomy-owned)
        return aliased
    parts = slug.split(".")
    candidates: list[str] = []
    # 1. toggle trailing 's' on the last segment (same depth — most specific)
    last = parts[-1]
    toggled = last[:-1] if last.endswith("s") else last + "s"
    candidates.append(".".join(parts[:-1] + [toggled]))
    # 2. drop a spurious intermediate segment (keeps specificity)
    if len(parts) >= 3:
        for i in range(1, len(parts) - 1):
            candidates.append(".".join(parts[:i] + parts[i + 1:]))
    # 3. drop a spurious child (the parent) — accepted below ONLY if it is itself a LEAF
    if len(parts) >= 2:
        candidates.append(".".join(parts[:-1]))
    for c in candidates:
        if c in leaves:            # a REAL LEAF only — never a coarse (non-leaf) ancestor
            return c
    return None


def guard_valid(primary: str, valid_leaves: list[str]) -> str | None:
    """VALID-SLUG GUARD — CORRECTNESS OVER COVERAGE. A story is kept when its primary is
    something we actually offer (a real leaf, or a whole top-level bucket it genuinely
    belongs to), OR when a near-miss slug SNAPS to a real leaf (see _snap_to_leaf: plural
    drift, a spurious level — but never a coarse ancestor). Anything else — an off-taxonomy
    subject (ice hockey, crime, a region we don't offer) or the DROP sentinel — returns
    None and the story is EXCLUDED."""
    if not primary:
        return None
    p = primary.strip()
    if p.lower() == DROP:
        return None
    if p in offered_targets(valid_leaves):
        return p
    return _snap_to_leaf(p, set(valid_leaves))


def _model() -> str:
    return settings.fallback_model  # Haiku (claude-haiku-4-5-…)


def build_system(valid_leaves: list[str]) -> str:
    """The STATIC instruction + valid-leaf catalog. Identical for every story in a run,
    so it is sent as a cache_control'd system block — Anthropic prompt-caches it and each
    subsequent story pays ~0.1x for this (much larger) half of the prompt (see cost model)."""
    leaves = sorted(valid_leaves)
    tops = sorted({leaf.split(".")[0] for leaf in leaves})
    catalog = "\n".join(leaves)
    buckets = ", ".join(tops)
    return (
        "You are a strict news categoriser. Your ONE job is to place each story in the "
        "category that best fits its SUBJECT — nothing else. Judge by the STORY CONTENT; "
        "the pool hints are weak context that is often wrong and must never override it.\n\n"
        f"ALLOWED LEAF SLUGS (prefer the most specific one that genuinely fits the subject):\n{catalog}\n\n"
        f"ALLOWED TOP-LEVEL BUCKETS (use ONLY for a genuinely GENERAL story of that whole "
        f"area when no leaf fits): {buckets}\n\n"
        "RETURN \"none\" (drop) ONLY FOR A TAXONOMY GAP — the story's SUBJECT has no home in "
        "the list above:\n"
        "- a topic/sport not listed (e.g. ICE HOCKEY / NHL — there is no hockey slug),\n"
        "- CRIME or an ACCIDENT with no topical category (a road death, a local assault, a "
        "fire) — unless it genuinely belongs to a listed topic,\n"
        "- a COUNTRY/REGION we don't offer (e.g. a Latin-America-only story when no "
        "top-stories.<region> covers it).\n"
        "Do NOT file such a story under a loosely-related generic bucket — return \"none\".\n\n"
        "NEWSWORTHINESS IS NOT YOUR JOB — NEVER return \"none\" because a story seems trivial, "
        "minor, gossipy, rumoured, unconfirmed, speculative, tabloid, or low-quality. "
        "Importance and quality are judged by a LATER editorial step, not here. If ANY listed "
        "category covers the subject, you MUST assign it. Examples: a celebrity wedding or "
        "relationship rumour → culture.celebrity (it IS about a celebrity); a product reissue "
        "→ its technology/business leaf; a minor politician's remark → politics.<region>. "
        "Assigning the correct category is NEVER 'mis-filing'.\n\n"
        "OTHER RULES:\n"
        "- primary MUST be copied verbatim (a leaf, a top-level bucket, or \"none\"). Never "
        "invent/abbreviate a slug; never use a legacy slug (world.*, entertainment.*, "
        "climate, politics.global).\n"
        "- Judge the actual subject. A word like 'shares' (to share), 'summit', or a city "
        "name that is also a football club must NOT drive the category unless the story is "
        "genuinely about that topic.\n"
        "- POLITICS is region-aware: politics.uk / politics.us / politics.europe by the "
        "country the politics concerns; anywhere else (India, Africa, Asia, Middle East, "
        "global) → politics.world.\n"
        "- secondary: 0-2 other allowed slugs that also apply. Omit if none.\n\n"
        "Respond with JSON only:\n"
        '{"primary": "<allowed slug or none>", "secondary": ["<allowed slug>", ...], '
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
    """Classify one story.

    Returns:
      • a StoryCategorisation with a valid primary   → KEEP,
      • DROPPED (primary == DROP)                     → EXCLUDE (no honest taxonomy fit),
      • None                                          → API/parse FAILURE (caller may fall
                                                        back operationally).

    DROP and failure are distinct: a dropped story must be excluded, NOT re-filed by a
    fallback. The returned KEEP primary is guaranteed to be something we offer."""
    offered = offered_targets(valid_leaves)
    system = build_system(valid_leaves)
    user = build_user(title=title, snippets=snippets, pool_topic=pool_topic,
                      pool_country=pool_country)
    raw = _post_anthropic(system, user)
    if raw is None:
        return None
    parsed = _parse(raw, offered)
    if parsed is None:
        return None
    raw_primary, secondary, confidence, reason = parsed

    # ── VALID-SLUG GUARD — CORRECTNESS OVER COVERAGE ─────────────────────────────
    # Keep on a genuine offered target, or SNAP a near-miss to a real leaf (plural drift,
    # a spurious level); otherwise DROP. Never reroute an off-taxonomy subject to a coarse
    # ancestor.
    primary = guard_valid(raw_primary, valid_leaves)
    if primary is None:
        logger.info("llm_categoriser DROP %r (no taxonomy fit) for %r", raw_primary, title[:60])
        # Preserve the model's reason for visibility (dry-run / logs); still a DROP result.
        return StoryCategorisation(
            primary=DROP, secondaries=[], confidence=confidence,
            reason=reason or "no taxonomy fit", method="llm-drop", raw_slug=raw_primary,
        )
    secondary = [s for s in secondary if s in offered and s != primary][:2]

    return StoryCategorisation(
        primary=primary,
        secondaries=secondary,
        confidence=confidence,
        reason=reason,
        method="llm-primary" if primary == raw_primary else "llm-primary-rerouted",
        raw_slug=raw_primary,
    )
