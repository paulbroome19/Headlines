"""
LLM-generated connective tissue for bulletins.

Two ways in:

  • generate_connective()  — the WHOLE bulletin (greeting + every transition + outro)
    in ONE Sonnet call. Used by the synchronous (cached / non-streaming) assembly path
    where nothing is gating first-play.

  • The SPLIT path for streaming first-play (data.run_background_assembly):
      – generate_greeting()          greeting ALONE, via Haiku, from the lead HEADLINE only
                                     (no summaries) so it fires at T0 and unblocks the intro
                                     TTS — nothing except the greeting itself gates first-play.
      – generate_bridges_and_outro() every non-lead transition + the outro, in the BACKGROUND
                                     after the start-pack, fed the already-written greeting so
                                     the whole briefing keeps one voice.

Every prompt shares one persona/register block (`_VOICE`) so the greeting,
transitions, and outro never drift apart. Falls back to templates (via assembler)
when the API is unavailable or a response is malformed.

Configuration (via settings / environment variables):
  CONNECTIVE_MODEL   Optional. Default: settings.connective_model (Sonnet). Greeting+outro.
  GREETING_MODEL     Optional. Default: settings.fallback_model (Haiku). Greeting only.
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
from core.pipeline.data.voice import VOICE_BLOCK
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


def _greeting_model() -> str:
    """Model for the greeting-only call — Haiku by default (fast + cheap). The greeting is
    short and depends only on the lead headline, so it never needs the heavier connective
    model, and keeping it off the Sonnet path is what unblocks first-play. Override via
    GREETING_MODEL."""
    import os
    return os.environ.get("GREETING_MODEL") or settings.fallback_model


# ── The VOICE — one shared persona/register block for EVERY spoken-tissue prompt ──────────
# The greeting, the bridges, the outro, AND the story summariser all paste in the SAME block
# (core.pipeline.data.voice.VOICE_BLOCK), so there's no seam between a story body and the tissue
# around it. The voice changes in ONE place. NOTE: this feeds the PROMPTS only — the greeting
# still runs on Haiku (generate_greeting, _greeting_model) at T0, unchanged.
_VOICE = VOICE_BLOCK


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
        _VOICE + "\n\n"
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


def _post_messages(prompt: str, *, max_tokens: int, model: str | None = None) -> dict | None:
    """POST one user message to an Anthropic model; return the parsed JSON response dict, or
    None on missing key / HTTP / network failure (caller falls back). `model` defaults to the
    connective (Sonnet) model; the greeting call passes the Haiku model."""
    api_key = settings.anthropic_api_key
    if not api_key:
        logger.debug("connective: ANTHROPIC_API_KEY not set — skipping LLM generation")
        return None
    body = json.dumps({
        "model": model or _model(),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        _API_URL, data=body,
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
        logger.warning("connective: LLM unavailable (HTTP %s: %s) — falling back", e.code, e.reason)
    except Exception as e:
        logger.warning("connective: LLM unavailable (%s) — falling back", e)
    return None


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
    prompt = _build_prompt(stories=stories, name=name, now=now, is_first_bulletin=is_first_bulletin)
    raw = _post_messages(prompt, max_tokens=_MAX_TOKENS)
    if raw is None:
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


# ── Split path 1/2: the GREETING alone (Haiku, from the lead headline) ────────────────────
# First-play gates ONLY on the greeting + the lead opener. The greeting needs nothing but the
# time of day, the name, and the lead HEADLINE — no story summaries — so it can be generated at
# T0 (before/alongside summarisation) on a fast Haiku call and its TTS started immediately. This
# is what takes the ~70s connective-Sonnet call off the first-play critical path.


def _build_greeting_prompt(
    *,
    lead_headline: str,
    trail_headlines: list[str],
    name: str | None,
    now: datetime,
    is_first_bulletin: bool,
) -> str:
    hour = now.hour
    time_word = _time_of_day(hour)
    name_str = name if name else "no name provided (skip personalisation)"
    trail = [h for h in (trail_headlines or []) if h]
    trail_block = "\n".join(f"  - {h!r}" for h in trail) if trail else "  (none)"

    return (
        _VOICE + "\n\n"
        "You are writing ONLY the GREETING that opens an audio news briefing — the warm menu that "
        "points the listener at what's coming, then hands into the news. The stories themselves are "
        "written separately, in this same voice; you write just the opening, so there's no seam.\n\n"
        "Context:\n"
        f"- Listener's name: {name_str}\n"
        f"- Time of day: {time_word} (raw hour: {hour})\n"
        f"- Is this their first-ever bulletin: {is_first_bulletin}\n"
        f"- LEAD story headline: {lead_headline!r}\n"
        f"- Other headlines you MAY lightly touch (optional):\n{trail_block}\n\n"
        "GREETING:\n"
        "Greet the listener for the UK time of day, then TRAIL the bulletin — like a radio "
        "'coming up...' — then hand in. The greeting is a MENU, not the first course: it points at "
        "stories, it does NOT report them in full.\n"
        "- Personal greeting, name once if available (e.g. \"Evening, Paul.\" / \"Hi Paul.\"), using "
        "the UK time of day. If no name, greet naturally without one.\n"
        "- Lead with the LEAD headline: a topic phrase plus the single most striking thing IN that "
        "headline. Optionally touch ONE of the other headlines above with a light topic phrase — no "
        "more, and never trail everything.\n"
        "- Work ONLY from the headlines given — do NOT invent facts, numbers, names, or details "
        "beyond them.\n"
        "- End with a short, natural hand-off into the bulletin (vary it: \"Here's the latest.\" / "
        "\"Let's get into it.\" / similar).\n"
        + (
            "This is the listener's first-ever bulletin — give a slightly warmer welcome than usual.\n"
            if is_first_bulletin
            else ""
        )
        + "Shape (do NOT copy the wording — vary it every time): \"Morning, Paul. Top of the list, a "
        "big earthquake in Venezuela — plus a telecoms merger worth a look. Here's the latest.\"\n"
        "Trail the lead, don't report it: name the topic and the single most striking thing in the "
        "headline, no more. The banned patterns and TTS rules in the voice above apply.\n\n"
        "Output ONLY valid JSON — no markdown fences, no commentary:\n"
        '{"greeting":"..."}'
    )


def generate_greeting(
    *,
    lead_headline: str,
    trail_headlines: list[str] | None = None,
    name: str | None,
    now: datetime,
    is_first_bulletin: bool,
) -> str | None:
    """Generate ONLY the greeting/intro, via a fast Haiku call, from the lead headline (plus a
    couple of trail headlines) — no story summaries required, so this can fire at T0 and unblock
    the intro TTS. Returns the greeting text, or None on failure (caller falls back to a template
    intro)."""
    prompt = _build_greeting_prompt(
        lead_headline=lead_headline, trail_headlines=trail_headlines or [],
        name=name, now=now, is_first_bulletin=is_first_bulletin,
    )
    raw = _post_messages(prompt, max_tokens=400, model=_greeting_model())
    if raw is None:
        return None
    try:
        text_content = raw["content"][0]["text"].strip()
        if text_content.startswith("```"):
            text_content = "\n".join(
                l for l in text_content.splitlines() if not l.strip().startswith("```")
            )
        parsed = json.loads(text_content)
        greeting = str(parsed["greeting"]).strip()
        if not greeting:
            raise ValueError("greeting is empty")
    except (KeyError, IndexError, ValueError, json.JSONDecodeError, TypeError) as e:
        logger.warning("greeting: malformed (%s) — falling back to template intro", e)
        return None
    logger.info("greeting: generated  model=%s  len=%d", _greeting_model(), len(greeting))
    return greeting


# ── Split path 2/2: every bridge + the outro, in the BACKGROUND ───────────────────────────
# Generated AFTER the start-pack is synthesising, in one Sonnet call fed the already-written
# greeting so the whole bulletin keeps one voice (continuity across bridges is why this is one
# call, not per-story). Nothing here can gate first-play — it runs entirely after safe_to_start
# is achievable. The lead needs no bridge (the greeting leads into it).


def _build_bridges_prompt(
    *,
    stories: list[dict],
    prior_greeting: str,
    name: str | None,
    now: datetime,
) -> str:
    """Prompt for EVERY non-lead story's transition + the outro, continuing the voice of the
    already-written greeting."""
    hour = now.hour
    time_word = _time_of_day(hour)
    order = [str(s["story_id"]) for s in stories]
    remaining = order[1:]  # every non-lead bridge; the lead leads straight from the greeting

    lines: list[str] = []
    for s in stories:
        sid = str(s["story_id"])
        hook = _extract_hook(s)
        descriptor = s.get("headline") or hook or "(no descriptor)"
        category = (s.get("primary_category") or "general").split(".")[0]
        lines.append(f"  Story {sid}: [{category}] {descriptor!r}\n    Hook: {hook!r}")
    stories_block = "\n".join(lines)
    remaining_ids = ", ".join(f'"{sid}"' for sid in remaining)
    example = json.dumps({sid: "..." for sid in remaining})

    return (
        _VOICE + "\n\n"
        "The GREETING that opens this bulletin has already been written (below). CONTINUE in "
        "exactly that voice — same warmth, rhythm, and register. Do not reset the tone; the "
        "listener must not hear a seam between the greeting and these bridges.\n\n"
        f"Listener's name: {name if name else 'none (skip personalisation)'}\n"
        f"UK time of day: {time_word} (hour {hour}).\n\n"
        f"GREETING (already written — do NOT rewrite):\n{prior_greeting!r}\n\n"
        f"Full running order (FIXED, do not reorder): [{', '.join(order)}]\n"
        f"Stories:\n{stories_block}\n\n"
        "YOUR TASK: write ONLY (a) the transition that plays immediately BEFORE each of these "
        f"stories, in this fixed order: [{remaining_ids}], and (b) the outro. The LEAD story needs "
        "no bridge (the greeting leads straight into it), so do not write one for it.\n"
        "- Each bridge references real content from the PREVIOUS story in the fixed order — a light "
        "pivot, not a recap.\n"
        "- A bridge pivots FROM the previous story INTO the next. It must NEVER restate the next "
        "story's opening line — the body delivers that; you only set it up. One sentence, two at "
        "most.\n"
        "- Vary naturally; never formulaic. Handle tonal shifts (e.g. into a sports result) with "
        "grace — never \"On a lighter note!\". Some bridges can be very short or \"\".\n"
        "OUTRO: close warmly, name optional. Point at a SPECIFIC story to follow if any, never a "
        f"characterisation of the day. UK time-aware sign-off ({time_word}). No story counts, no "
        "durations, no commentary on the day's news volume.\n"
        "TTS: numbers/times as spoken words (\"fourteen hundred\", not \"1400\").\n\n"
        "Output ONLY valid JSON in this exact shape — no markdown fences, no commentary:\n"
        '{"transitions":' + example + ',"outro":"..."}'
    )


def generate_bridges_and_outro(
    *,
    stories: list[dict],
    prior_greeting: str,
    name: str | None,
    now: datetime,
) -> tuple[dict[str, str], str] | None:
    """Background (post-gate) generation of EVERY non-lead story's transition + the outro, in one
    Sonnet call fed the already-written greeting so the whole briefing keeps one voice. NEVER on
    the first-play path. Returns ({story_id: transition}, outro) covering stories from index 1
    onward, or None on failure (caller falls back to silent bridges + a template outro)."""
    order = [str(s["story_id"]) for s in stories]
    remaining = order[1:]  # empty for a single-story bulletin → only the outro is generated
    prompt = _build_bridges_prompt(stories=stories, prior_greeting=prior_greeting, name=name, now=now)
    raw = _post_messages(prompt, max_tokens=_MAX_TOKENS)
    if raw is None:
        return None
    try:
        text_content = raw["content"][0]["text"].strip()
        if text_content.startswith("```"):
            text_content = "\n".join(
                l for l in text_content.splitlines() if not l.strip().startswith("```")
            )
        parsed = json.loads(text_content)
        outro = str(parsed["outro"]).strip()
        if not outro:
            raise ValueError("outro is empty")
        transitions_raw = parsed.get("transitions") or {}
        if not isinstance(transitions_raw, dict):
            raise ValueError("transitions is not a dict")
        transitions = {sid: str(transitions_raw[sid]) for sid in remaining if sid in transitions_raw}
    except (KeyError, IndexError, ValueError, json.JSONDecodeError, TypeError) as e:
        logger.warning("bridges: malformed (%s) — falling back to templates", e)
        return None
    logger.info("bridges: generated  bridges=%d  outro_len=%d", len(transitions), len(outro))
    return transitions, outro
