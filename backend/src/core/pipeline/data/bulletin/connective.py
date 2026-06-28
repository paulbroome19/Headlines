"""
LLM-generated connective tissue for bulletins.

Generates greeting, per-story transitions, and outro in a single Anthropic
Sonnet call so they cohere across the full bulletin. Falls back to templates
(via assembler) when the API is unavailable or the response is malformed.

Configuration (via settings / environment variables):
  CONNECTIVE_MODEL   Optional. Default: settings.connective_model.
  ANTHROPIC_API_KEY  Required. Absent → returns None (fall back to templates).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from core.platform.config.settings import settings
from core.pipeline.data.bulletin.intros import _extract_hook

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 700
_TIMEOUT_SECONDS = 20


def uk_now() -> datetime:
    """Return the current time in Europe/London (BST-aware). Always tz-aware."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/London"))
    except Exception:
        return datetime.now(timezone.utc)


def _model() -> str:
    import os
    return os.environ.get("CONNECTIVE_MODEL") or settings.connective_model


@dataclass(frozen=True)
class ConnectiveResult:
    greeting: str
    transitions: list[str]   # one per story; index 0 is the "before story 1" slot (always "")
    outro: str


def _time_of_day(hour: int) -> str:
    if hour < 12:
        return "morning"
    elif hour <= 16:
        return "afternoon"
    elif hour <= 21:
        return "evening"
    else:
        return "night"


def _est_spoken_duration(n: int) -> str:
    """Rough spoken duration estimate for n stories."""
    seconds = n * 35
    mins = round(seconds / 60)
    if mins <= 1:
        return "about a minute"
    return f"about {mins} minutes"


def _build_prompt(
    *,
    stories: list[dict],
    name: str | None,
    now: datetime,
    is_first_bulletin: bool,
) -> str:
    hour = now.hour
    time_word = _time_of_day(hour)
    n = len(stories)
    duration = _est_spoken_duration(n)
    name_str = name if name else "no name provided (skip personalisation)"

    story_lines: list[str] = []
    for i, story in enumerate(stories):
        hook = _extract_hook(story)
        descriptor = story.get("headline") or hook or "(no descriptor)"
        category = (story.get("primary_category") or "general").split(".")[0]
        story_lines.append(
            f"  Story {i + 1}: [{category}] {descriptor!r}\n"
            f"    Hook: {hook!r}"
        )
    stories_block = "\n".join(story_lines)

    transitions_example = '["", "' + '", "'.join("..." for _ in range(n - 1)) + '"]' if n > 1 else '[""]'

    return (
        "You are a calm, intelligent morning-briefing host — a trusted person catching "
        "the listener up, not a hype radio DJ. You use contractions, write for natural "
        "rhythm, and avoid broadcast stiffness.\n\n"
        "Context for this bulletin:\n"
        f"- Listener's name: {name_str}\n"
        f"- Time of day: {time_word} (raw hour: {hour})\n"
        f"- Is this their first-ever bulletin: {is_first_bulletin}\n"
        f"- Number of stories: {n} ({duration})\n"
        f"- Stories (ordered):\n{stories_block}\n\n"
        "Your task: generate the greeting, one transition per story, and the outro — "
        "all as natural spoken text ready for TTS synthesis.\n\n"
        "GREETING:\n"
        "Greeting FIRST, then ease into the news.\n"
        "Pattern: greet by name → orient (how many stories, rough length) → lead naturally into story 1.\n"
        "Exact contrast:\n"
        "  WRONG: \"Markets opened green this morning. Morning, Paul.\"\n"
        "  RIGHT: \"Morning, Paul. Five stories for you today — let's start with the markets, "
        "which opened green.\"\n"
        + (
            "This is the listener's first-ever bulletin — give a warmer welcome than usual.\n"
            if is_first_bulletin
            else ""
        )
        + "\n"
        "TRANSITIONS:\n"
        f"Return exactly {n} transitions (array length MUST equal {n}).\n"
        "- Index 0 must always be \"\" (nothing precedes story 1).\n"
        + (
            f"- Indices 1 to {n - 1} bridge from the PREVIOUS story to the NEXT story.\n"
            if n > 1
            else ""
        )
        + "- Be genuinely context-aware: reference real content where it helps "
        "(\"From Westminster to the pitch...\").\n"
        "- Vary naturally. NEVER formulaic.\n"
        "- Handle tonal shifts with real sensitivity. Moving from tragedy or conflict "
        "to a sports result must feel deliberate and graceful — never a jarring "
        "\"On a lighter note!\".\n"
        "- Some transitions can be very short or \"\" (a brief pause) — not every one "
        "needs a full sentence.\n\n"
        "OUTRO:\n"
        "Warm close. Use the name sometimes (not always). Be time-aware — it is currently "
        f"{time_word} in the UK (hour {hour}). Examples: \"catch you this evening\" in the "
        "morning, \"sleep well\" at night. Forward-looking but not vague.\n\n"
        "TTS RULES (critical — this is read aloud):\n"
        "- Write numbers and times as spoken words (\"five stories\", not \"5\").\n"
        "- Keep names naturally spelled (a downstream layer handles problem names).\n\n"
        "Output ONLY valid JSON in this exact shape — no markdown fences, no commentary:\n"
        "{\"greeting\":\"...\",\"transitions\":" + transitions_example + ",\"outro\":\"...\"}"
    )


def generate_connective(
    *,
    stories: list[dict],
    name: str | None,
    now: datetime,
    is_first_bulletin: bool,
) -> ConnectiveResult | None:
    """
    Generate greeting, transitions, and outro via a single Sonnet call.

    Returns None if the API key is absent, the call fails, or the response
    is malformed — the assembler will fall back to templates.

    stories:           ordered story dicts (each has audio_script, primary_category;
                       optionally headline).
    name:              listener's first name, or None.
    now:               current UK time (from uk_now()).
    is_first_bulletin: True on the listener's very first bulletin.
    """
    api_key = settings.anthropic_api_key
    if not api_key:
        logger.debug(
            "connective: ANTHROPIC_API_KEY not set — skipping LLM connective generation"
        )
        return None

    prompt = _build_prompt(stories=stories, name=name, now=now, is_first_bulletin=is_first_bulletin)
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
        reason = f"HTTP {e.code}: {e.reason}"
        logger.warning(
            "connective: LLM unavailable/malformed (%s) — falling back to templates",
            reason,
        )
        return None
    except Exception as e:
        logger.warning(
            "connective: LLM unavailable/malformed (%s) — falling back to templates",
            e,
        )
        return None

    try:
        text_content = raw["content"][0]["text"].strip()
        # Strip markdown fences if present (mirrors llm_summariser.py)
        if text_content.startswith("```"):
            lines = text_content.splitlines()
            text_content = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            )
        parsed = json.loads(text_content)

        greeting = str(parsed["greeting"]).strip()
        outro = str(parsed["outro"]).strip()
        transitions = list(parsed["transitions"])

        if not greeting:
            raise ValueError("greeting is empty")
        if not outro:
            raise ValueError("outro is empty")
        if len(transitions) != len(stories):
            raise ValueError(
                f"transitions length {len(transitions)} != stories length {len(stories)}"
            )

        # Coerce index 0 to "" — nothing precedes story 1
        if transitions and transitions[0]:
            transitions[0] = ""

        transitions = [str(t) for t in transitions]

    except (KeyError, IndexError, ValueError, json.JSONDecodeError, TypeError) as e:
        logger.warning(
            "connective: LLM unavailable/malformed (%s) — falling back to templates",
            e,
        )
        return None

    logger.info(
        "connective: generated  stories=%d  model=%s",
        len(stories), _model(),
    )
    return ConnectiveResult(greeting=greeting, transitions=transitions, outro=outro)
