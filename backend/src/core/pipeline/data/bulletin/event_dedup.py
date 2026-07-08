"""
event_dedup.py — TACTICAL fix for issue #35 (REMOVABLE once #36 lands).

A band-aid at bulletin assembly: a cheap LLM (Haiku) pass that collapses
SELECTED stories which cover the SAME underlying real-world event into one,
keeping the best-ranked representative.

Why: keyword/hint clustering keys on the first significant headline word, so
paraphrased duplicates ("At least 11 dead in factory blast" vs "Eleven killed
in factory explosion") never merge upstream — the same event can reach a
bulletin as two stories. This pass catches them just before the connective
tissue and assembly run, so the event is reported once and the greeting /
transitions never reference it twice.

Placement: AFTER ranking/selection picks the stories, BEFORE summarisation's
output is used by connective + assembly. Runs on cache miss only (one Haiku
call per fresh bulletin).

SAFETY — conservative by design:
  * Only CLEAR same-event cases are merged. A false merge silently deletes real
    news, which is worse than a missed duplicate — so when uncertain we DON'T
    merge.
  * Any failure (no key, network error, timeout, malformed output) → ONE retry,
    then return the input list UNCHANGED (no LLM dedup) with a LOUD (ERROR) log —
    never a silent fail-open. A failed dedup must never break generation, and the
    deterministic whole-bulletin backstop (selection.dedup_bulletin_events) then
    removes any same-event duplicate the LLM would have caught.
  * Never drops below one story (each group collapses to its representative).

REMOVE THIS MODULE and its single call site in routes/data.py when issue #36
(embeddings-based semantic clustering) lands — that fixes duplicates upstream at
clustering time and makes this band-aid unnecessary.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from core.platform.config.settings import settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 400
_TIMEOUT_SECONDS = 20


def _model() -> str:
    # Haiku is plenty for this classification task. Env override for tuning.
    return os.environ.get("DEDUP_MODEL") or settings.fallback_model


def dedup_same_event(stories: list[dict]) -> list[dict]:
    """
    Collapse stories covering the same real-world event, keeping the best-ranked
    representative of each group, and return a new list in the original ranked
    order with the duplicates removed.

    `stories` is expected in ranked order (index 0 = best-ranked); each item is a
    dict with at least a "headline" used for detection. The lowest-index member
    of each detected group is kept (the best-ranked representative); the rest are
    dropped.

    Never raises. On <2 stories, missing API key, or ANY LLM failure / malformed
    output, returns `stories` unchanged (no dedup), logged.
    """
    if len(stories) < 2:
        return stories

    api_key = settings.anthropic_api_key
    if not api_key:
        logger.info("event_dedup(#35): ANTHROPIC_API_KEY not set — skipping dedup (assembling all stories)")
        return stories

    titles = [(s.get("headline") or "").strip() for s in stories]
    if not any(titles):
        return stories  # nothing to compare on

    # ONE Haiku call with a single retry. On repeated failure (network OR malformed output) this
    # logs LOUDLY and returns no groups — it NEVER silently fails open. The deterministic
    # whole-bulletin backstop (selection.dedup_bulletin_events) then catches same-event duplicates.
    groups = _dedup_groups(api_key, titles, n=len(stories))
    if not groups:
        return stories

    # Keep the best-ranked (lowest index) of each group; drop the others.
    drop: set[int] = set()
    for g in groups:
        keep = min(g)
        for idx in g:
            if idx != keep:
                drop.add(idx)
    if not drop:
        return stories

    deduped = [s for i, s in enumerate(stories) if i not in drop]
    merged = [
        "kept[%d]=%r dropped=%s" % (
            min(g), titles[min(g)][:70], [titles[i][:50] for i in g if i != min(g)]
        )
        for g in groups if len(g) > 1
    ]
    logger.info("event_dedup(#35): %d → %d stories; merged %s", len(stories), len(deduped), merged)
    return deduped


def _post_dedup(api_key: str, titles: list[str]) -> dict | None:
    """One Anthropic call. Returns the parsed response, or None on network/timeout/HTTP error."""
    body = json.dumps({
        "model": _model(),
        "max_tokens": _MAX_TOKENS,
        "temperature": 0,   # same-event detection is a classification — deterministic
        "messages": [{"role": "user", "content": _build_prompt(titles)}],
    }).encode()
    req = urllib.request.Request(
        _API_URL, data=body,
        headers={"x-api-key": api_key, "anthropic-version": _API_VERSION,
                 "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read())
    except Exception as e:  # network / timeout / HTTP
        logger.warning("event_dedup(#35): LLM call failed (%s)", e)
        return None


def _dedup_groups(api_key: str, titles: list[str], *, n: int) -> list[list[int]]:
    """The LLM same-event pass with a SINGLE retry. Returns validated 0-based groups, or [] after
    a loud error. Retries once on EITHER a failed call OR malformed output; on the second failure
    it logs at ERROR (never a silent fail-open) and returns [] so the caller assembles all stories
    and the deterministic backstop removes any same-event duplicate."""
    last_err = "unknown"
    for attempt in (1, 2):
        raw = _post_dedup(api_key, titles)
        if raw is None:
            last_err = "LLM unavailable"
        else:
            try:
                return _parse_groups(raw, n=n)
            except Exception as e:  # malformed / out-of-range / overlapping groups
                last_err = f"malformed output ({e})"
        if attempt == 1:
            logger.warning("event_dedup(#35): %s — retrying once", last_err)
    logger.error(
        "event_dedup(#35): dedup FAILED after retry (%s) — no LLM dedup this bulletin; the "
        "deterministic backstop (dedup_bulletin_events) must catch same-event duplicates", last_err,
    )
    return []


def _parse_groups(raw: dict, n: int) -> list[list[int]]:
    """Parse the model's JSON into validated 0-based groups. Raises on anything off."""
    text_content = raw["content"][0]["text"].strip()
    if text_content.startswith("```"):  # strip markdown fences (mirrors other LLM callers)
        text_content = "\n".join(
            line for line in text_content.splitlines() if not line.strip().startswith("```")
        )
    # Tolerate any trailing prose after the JSON object (decode the first value, ignore the rest).
    start = text_content.find("{")
    parsed, _ = json.JSONDecoder().raw_decode(text_content[start:]) if start >= 0 else ({}, 0)
    groups_raw = parsed["groups"]
    if not isinstance(groups_raw, list):
        raise ValueError("groups is not a list")

    groups: list[list[int]] = []
    seen: set[int] = set()
    for g in groups_raw:
        if not isinstance(g, list) or len(g) < 2:
            continue  # a singleton (or junk) is not a merge — ignore it
        idxs: list[int] = []
        for x in g:
            i = int(x) - 1  # model uses 1-based numbering
            if i < 0 or i >= n:
                raise ValueError(f"index {x} out of range 1..{n}")
            if i in seen:
                raise ValueError(f"index {x} appears in more than one group")
            seen.add(i)
            idxs.append(i)
        groups.append(idxs)
    return groups


def _build_prompt(titles: list[str]) -> str:
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    return (
        "You are a careful news-desk editor de-duplicating a bulletin line-up.\n\n"
        "Below are headlines selected for ONE bulletin. Some may be the SAME "
        "real-world event reported by different outlets or paraphrased differently. "
        'For example, "At least 11 dead in factory blast" and "Eleven killed in '
        'factory explosion" are the SAME event; "Epstein documents released" and '
        '"Epstein files reveal new names" are the SAME event.\n\n'
        "Group ONLY headlines that report the SAME specific event — the same "
        "incident or occurrence. Be CONSERVATIVE:\n"
        "- DO group obvious paraphrases or different-outlet versions of one event.\n"
        "- Do NOT group headlines that merely share a topic, person, place, team, "
        "or category. Two DIFFERENT matches, two SEPARATE statements by the same "
        "person, two UNRELATED crimes, or two different developments in an ongoing "
        "saga are NOT the same event.\n"
        "- When in doubt, DO NOT group. A false merge silently deletes real news, "
        "which is far worse than leaving a possible duplicate.\n\n"
        f"HEADLINES:\n{numbered}\n\n"
        'Return ONLY JSON of the form {"groups": [[n, n, ...], ...]} where each '
        "inner list holds the numbers of headlines that are the SAME event (each "
        "group has 2 or more numbers). Headlines unique to themselves do NOT "
        'appear in any group. If there are no duplicates, return {"groups": []}. '
        "No prose, no markdown — JSON only."
    )
