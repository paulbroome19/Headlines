"""
Pre-TTS script normalisation.

Applied to the assembled bulletin script before it is sent to ElevenLabs.
Handles:
  - Currency / number shorthand  (£2.3bn → "2.3 billion pounds")
  - Percentage                   (10%  → "10 percent")
  - Acronyms that need spacing   (verified to trip the voice model)
  - Pronunciation overrides      (names / words the TTS mispronounces)
"""
from __future__ import annotations

import re

# ── Pronunciation overrides ────────────────────────────────────────────────────
# Maps exact strings (case-sensitive match) to the phonetic spelling the TTS
# engine handles better. Keep sorted alphabetically. Extend as new problems
# are discovered from listening sessions.

PRONUNCIATION_OVERRIDES: dict[str, str] = {
    # UK political names that TTS commonly stumbles on
    "Keir":          "Keer",
    "Starmer":       "Star-mer",
    "Tánaiste":      "Tah-nish-tuh",
    "Taoiseach":     "Tee-shock",
    "Sunak":         "Soo-nak",
    "Farage":        "Fa-rahj",
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


# ── Pronunciation overrides ────────────────────────────────────────────────────

def apply_pronunciation_overrides(text: str) -> str:
    for word, replacement in PRONUNCIATION_OVERRIDES.items():
        # Whole-word match, case-sensitive (proper nouns)
        text = re.sub(rf"\b{re.escape(word)}\b", replacement, text)
    return text


# ── Master normaliser ──────────────────────────────────────────────────────────

def normalise_for_tts(script: str) -> str:
    """Apply all normalisation passes in order before sending to TTS."""
    script = normalise_numbers(script)
    script = normalise_acronyms(script)
    script = apply_pronunciation_overrides(script)
    return script
