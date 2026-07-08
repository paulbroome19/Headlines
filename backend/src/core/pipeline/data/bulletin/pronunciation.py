"""
Pre-TTS script normalisation + the synthesis-time pronunciation lexicon.

Two distinct transforms live here, applied at DIFFERENT points in the pipeline
because of how they interact with the segment audio cache:

  1. normalise_for_tts()  — CONTENT normalisation, applied at ASSEMBLY time
     (assembler.py), BEFORE the segment script_hash is computed. Handles:
       - Currency / number shorthand  (£2.3bn → "2.3 billion pounds")
       - Percentage                   (10%  → "10 percent")
       - Acronyms that need spacing   (verified to trip the voice model)
     These are stable, deterministic content rewrites that are rarely touched,
     so it is fine for them to participate in the cache key.

  2. apply_pronunciation_lexicon()  — RENDERING tweak, applied at SYNTHESIS time
     (tts_client.synthesize()), AFTER script_hash is computed. Maps written
     names/words to a phonetic respelling the voice renders correctly.

WHY the lexicon is applied post-hash: it is the frequently-edited part (testers
report new mispronunciations regularly). If it fed the cache key, every lexicon
edit would re-hash — and therefore re-bill via ElevenLabs — every affected
segment. Applying it after hashing means an edit only changes what fresh renders
say; already-cached segments keep their old audio until the news naturally turns
the cache over. See tts_client.synthesize() for the exact call site.
"""
from __future__ import annotations

import re

# ── Pronunciation lexicon (applied at synthesis time — see module docstring) ────
# Maps a written form → the spoken (phonetic) respelling the voice renders
# correctly. Matching is WHOLE-WORD (\b…\b, never substrings) and CASE-SENSITIVE:
# entries are matched exactly as written. Case-sensitivity is deliberate — it
# preserves the written case and avoids mangling lowercase homographs (e.g.
# "Musk" the person vs "musk" the scent; "Niger" the country vs a substring).
#
# HOW TO ADD AN ENTRY (when a tester reports a mispronounced name):
#   1. Add   "WrittenForm": "Pho-net-ic",   below, grouped with its neighbours.
#   2. Pick the respelling by EAR against the actual voice — render a short
#      sample first (see docs/pronunciation-lexicon.md for the one-liner).
#      eleven_multilingual_v2 does NOT support SSML <phoneme> tags, so plain
#      respelling (hyphenated syllables, CAPS for stress) is the only lever.
#   3. That's it — no cache flush needed. Fresh renders pick it up; cached
#      segments keep their old audio until they naturally re-render.

PRONUNCIATION_LEXICON: dict[str, str] = {
    # UK political names that TTS commonly stumbles on
    "Keir":          "Keer",
    "Starmer":       "Star-mer",
    "Tánaiste":      "Tah-nish-tuh",
    "Taoiseach":     "Tee-shock",
    "Sunak":         "Soo-nak",
    "Farage":        "Fa-rahzh",
    "Reeves":        "Reeves",          # fine — entry as documentation anchor
    # Place names
    "Kyiv":          "Keev",
    "Qatar":         "Kah-tar",
    "Niger":         "Nee-zhair",       # country, not the river
    "Worcestershire": "Woo-ster-sheer",
    "Gloucester":    "Gloss-ter",
    "Leicester":     "Lester",
    "Edinburgh":     "Ed-in-bruh",
    # Sport
    "Verstappen":    "Ver-STAP-en",
    "Szoboszlai":    "So-bos-lay",
    "Haaland":       "Haw-lan",
    # Tech
    "Musk":          "Musk",            # fine — anchor
    "Zuckerberg":    "Zucker-berg",
}

# ── Number / currency patterns ─────────────────────────────────────────────────

_CURRENCY_SYMBOLS: dict[str, str] = {
    "£": "pounds",
    "$": "dollars",
    "€": "euros",
}

_SCALE: dict[str, str] = {
    "bn": "billion",
    "B":  "billion",
    "m":  "million",
    "M":  "million",
    "k":  "thousand",
    "K":  "thousand",
    "tr": "trillion",
    "T":  "trillion",
}

# Matches: £2.3bn  $45m  €1.2tr  2.3bn  45m  etc.
_NUMBER_PATTERN = re.compile(
    r"([£$€])?"          # optional currency symbol
    r"(\d[\d,]*(?:\.\d+)?)"  # number (with optional commas / decimals)
    r"(bn|B|tr|T|[mMkK])\b"  # scale suffix
)

# Percentage: 10%  10.5%
_PERCENT_PATTERN = re.compile(r"(\d[\d,]*(?:\.\d+)?)%")


_AMBIGUOUS_SINGLE = frozenset({"B", "m", "M", "k", "K", "T"})  # need currency context


def _replace_number(match: re.Match) -> str:
    symbol = match.group(1) or ""
    number = match.group(2).replace(",", "")
    scale_key = match.group(3)
    # Single-letter scales are ambiguous with units (100m = metres, 5K = run
    # distance). Only treat them as magnitudes when a currency symbol anchors
    # the value (£5m, $45M). Multi-char bn/tr are unambiguous and always expand.
    if scale_key in _AMBIGUOUS_SINGLE and not symbol:
        return match.group(0)
    scale = _SCALE.get(scale_key, scale_key)
    currency = _CURRENCY_SYMBOLS.get(symbol, "")
    parts = [number, scale]
    if currency:
        parts.append(currency)
    return " ".join(parts)


def normalise_numbers(text: str) -> str:
    text = _NUMBER_PATTERN.sub(_replace_number, text)
    text = _PERCENT_PATTERN.sub(lambda m: m.group(1) + " percent", text)
    return text


# ── Acronym handling ───────────────────────────────────────────────────────────
# ElevenLabs George handles most uppercase acronyms well (NHS, BBC, etc.)
# The ones below have been observed to need letter-spacing hints or are
# commonly confused. Add to this list as you discover new problems.
#
# Format: {acronym: space-separated spelling}
# e.g. "FBI" → "F B I"  causes the model to say "eff bee eye"

_ACRONYM_SPACE: dict[str, str] = {
    # These read poorly as words — force letter-by-letter
    "HMRC": "H M R C",
    "DVLA": "D V L A",
    "ASOS": "A S O S",   # often read as "ay-sos"
}

# Acronyms to leave as-is (pronounced as words — do NOT space them)
_ACRONYM_AS_WORD = frozenset([
    "NATO", "FIFA", "UEFA", "NASA", "UNICEF", "OPEC", "GCHQ",
    "OFSTED", "OFCOM", "IPSO",
])


def normalise_acronyms(text: str) -> str:
    for acronym, spaced in _ACRONYM_SPACE.items():
        text = re.sub(rf"\b{re.escape(acronym)}\b", spaced, text)
    return text


# ── Pronunciation lexicon ───────────────────────────────────────────────────────
# Built once from PRONUNCIATION_LEXICON: a single alternation so the whole text is
# rewritten in one pass (not one pass per entry). Longer keys first so that if one
# key is a prefix of another the longer wins. Case-sensitive (no re.IGNORECASE).


def _build_lexicon_pattern(lexicon: dict[str, str]) -> re.Pattern[str] | None:
    if not lexicon:
        return None
    keys = sorted(lexicon, key=len, reverse=True)
    alternation = "|".join(re.escape(k) for k in keys)
    return re.compile(rf"\b(?:{alternation})\b")


_LEXICON_PATTERN = _build_lexicon_pattern(PRONUNCIATION_LEXICON)


def apply_pronunciation_lexicon(text: str) -> str:
    """
    Replace known written forms with their spoken respelling.

    Applied at SYNTHESIS time (tts_client), AFTER the segment script_hash is
    computed — so editing the lexicon never changes a cache key. Whole-word,
    case-sensitive matching; substrings are never touched.
    """
    if _LEXICON_PATTERN is None:
        return text
    return _LEXICON_PATTERN.sub(lambda m: PRONUNCIATION_LEXICON[m.group(0)], text)


# ── Master normaliser (content only — the lexicon is applied later, post-hash) ──

def normalise_for_tts(script: str) -> str:
    """
    Content normalisation applied at ASSEMBLY time, before script_hash.

    NOTE: the pronunciation lexicon is deliberately NOT applied here — it runs at
    synthesis time (see apply_pronunciation_lexicon) so lexicon edits don't
    invalidate the audio cache. Only stable number/acronym rewrites live here.
    """
    script = normalise_numbers(script)
    script = normalise_acronyms(script)
    return script
