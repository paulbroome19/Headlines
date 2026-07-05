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
_MAX_TOKENS = 900
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
    order: list[str]              # story_ids in the model's chosen running order (permutation of input ids)
    transitions: dict[str, str]   # story_id -> transition text that plays BEFORE that story; first is ""
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
    name_str = name if name else "no name provided (skip personalisation)"

    story_lines: list[str] = []
    input_ids: list[str] = []
    for story in stories:
        sid = str(story["story_id"])
        input_ids.append(sid)
        hook = _extract_hook(story)
        descriptor = story.get("headline") or hook or "(no descriptor)"
        category = (story.get("primary_category") or "general").split(".")[0]
        story_lines.append(
            f"  Story {sid}: [{category}] {descriptor!r}\n"
            f"    Hook: {hook!r}"
        )
    stories_block = "\n".join(story_lines)
    ids_list = ", ".join(f'"{sid}"' for sid in input_ids)

    # Shape example: show the ids in input order (model should reorder them in output)
    example_order = json.dumps(input_ids)
    example_transitions = json.dumps({sid: ("" if i == 0 else "...") for i, sid in enumerate(input_ids)})

    return (
        "You are a calm, intelligent morning-briefing host — a trusted person catching "
        "the listener up, not a hype radio DJ. You use contractions, write for natural "
        "rhythm, and avoid broadcast stiffness.\n\n"
        "Context for this bulletin:\n"
        f"- Listener's name: {name_str}\n"
        f"- Time of day: {time_word} (raw hour: {hour})\n"
        f"- Is this their first-ever bulletin: {is_first_bulletin}\n"
        f"- Stories (use these ids exactly in your output):\n{stories_block}\n\n"
        "Your task: generate the greeting, choose the best narrative running order for "
        "the stories, write one transition per story, and write the outro — "
        "all as natural spoken text ready for TTS synthesis.\n\n"
        "GREETING:\n"
        "Greet the listener, then TRAIL the bulletin — like a radio 'coming up...' — then hand "
        "into the news. The greeting is a MENU, not the first course: it points at stories, it "
        "does NOT report them in full.\n"
        "- Personal greeting, name once if available (e.g. \"Evening, Paul.\" / \"Hi Paul.\"), using "
        "the UK time of day. If no name, greet naturally without one.\n"
        "- Trail only the TWO or THREE most compelling stories — NOT every story. The lead is the "
        "first id in your 'order'; also pick one or two others that hook well. On a long bulletin "
        "do NOT mention the rest — they're discovered as they play, so the greeting stays the same "
        "comfortable length whether there are three stories or eight.\n"
        "- WEIGHTED depth: the LEAD trailed story gets a topic phrase PLUS one concrete hook (a "
        "number, a name, the single key fact). The other one or two get just a LIGHT topic touch, "
        "no facts.\n"
        "- End with a short, natural hand-off into the bulletin (vary it: \"Here's the latest.\" / "
        "\"Let's get into it.\" / similar).\n"
        "Target (works the same for three stories or eight):\n"
        "  \"Hi Paul. Tonight we're covering Venezuela, where the earthquake death toll's climbed "
        "past fourteen hundred — plus a big telecoms merger, and why some business owners say "
        "they're earning less than their own staff. Here's the latest.\"\n"
        "BANNED in the greeting (these are the tells of a machine):\n"
        "- NO story counts (\"four stories\", \"three things\").\n"
        "- NO durations (\"should take two minutes\", \"in the next few minutes\").\n"
        "- NO commentary on the news as a category or on 'the day': never say things like \"heavy "
        "news day\", \"big day for news\", \"a lot going on today\", \"busy news day\", \"quiet day\", "
        "\"lots to get through\", or ANY sentence describing the day's overall news volume or "
        "texture. Stay INSIDE the content — lead with substance, never comment on the news from "
        "outside.\n"
        + (
            "This is the listener's first-ever bulletin — give a warmer welcome than usual.\n"
            if is_first_bulletin
            else ""
        )
        + "\n"
        "RUNNING ORDER:\n"
        f"The running order is FIXED — the stories play in exactly the order given above: "
        f"[{ids_list}]. Do NOT reorder them: the order reflects the listener's chosen filter "
        "priorities (their top categories first, sport last), not narrative preference. Your "
        "'order' array MUST equal that sequence exactly.\n\n"
        "TRANSITIONS:\n"
        "Return a 'transitions' object mapping EVERY story id to the bridge that plays "
        "immediately BEFORE that story in the FIXED order.\n"
        "- The FIRST story MUST have \"\" — the greeting leads directly into it.\n"
        + (
            "- Each subsequent bridge references real content from the previous story in the fixed order.\n"
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
        "Close warmly. Name optional (not every time).\n"
        "- If you point the listener at something to follow, point at a SPECIFIC story by name "
        "(e.g. \"Venezuela's the one to keep an eye on tomorrow\"), NEVER a characterisation of the "
        "day.\n"
        "- Keep genuine human warmth (e.g. \"take care of yourself tonight\").\n"
        f"- UK time-aware sign-off — it is currently {time_word} in the UK (hour {hour}): morning → "
        "\"catch you this evening\", evening → \"more tomorrow\", night → \"sleep well\".\n"
        "- SAME BAN as the greeting: no story counts, no durations, and NO commentary on the day's "
        "news texture or volume (\"heavy news day\" and all variants).\n"
        "Target (after a serious bulletin):\n"
        "  \"That's it for tonight, Paul. Venezuela's the one to keep an eye on as the rescue "
        "continues. More tomorrow — take care of yourself.\"\n\n"
        "TTS RULES (critical — this is read aloud):\n"
        "- Write numbers and times as spoken words (\"fourteen hundred\", not \"1400\").\n"
        "- Keep names naturally spelled (a downstream layer handles problem names).\n\n"
        "Output ONLY valid JSON in this exact shape — no markdown fences, no commentary:\n"
        "{\"greeting\":\"...\",\"order\":" + example_order + ",\"transitions\":" + example_transitions + ",\"outro\":\"...\"}\n"
        "Remember: 'order' MUST equal the given fixed sequence of ids (do NOT reorder); "
        "'transitions' maps every id to its pre-story bridge ('\"\"' for the first story)."
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

        if not greeting:
            raise ValueError("greeting is empty")
        if not outro:
            raise ValueError("outro is empty")

        # Running order is FIXED to the given filter-sequence order (top-stories → … →
        # sport). The LLM no longer reorders — it only writes greeting/transitions/outro —
        # so we take the input order verbatim and ignore any 'order' the model returned.
        order = [str(s["story_id"]) for s in stories]

        # Validate transitions: must be a dict with an entry for every id.
        transitions_raw = parsed["transitions"]
        if not isinstance(transitions_raw, dict):
            raise ValueError("transitions is not a dict")
        for sid in order:
            if sid not in transitions_raw:
                raise ValueError(f"transitions missing entry for story id {sid!r}")
        transitions = {sid: str(transitions_raw[sid]) for sid in order}

        # Coerce first story's transition to "" — the greeting leads directly into it.
        if order:
            transitions[order[0]] = ""

    except (KeyError, IndexError, ValueError, json.JSONDecodeError, TypeError) as e:
        logger.warning(
            "connective: LLM unavailable/malformed (%s) — falling back to templates",
            e,
        )
        return None

    logger.info(
        "connective: generated  stories=%d  order=%s  model=%s",
        len(order), order, _model(),
    )
    return ConnectiveResult(greeting=greeting, order=order, transitions=transitions, outro=outro)
