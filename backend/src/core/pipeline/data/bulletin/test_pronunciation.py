"""
Pronunciation lexicon: substitution correctness + the cache-key invariant.

The cache-key invariant is the whole point of this design: the lexicon is applied
at synthesis time (tts_client), AFTER script_hash is computed, so editing the
lexicon must NOT change what normalise_for_tts (the pre-hash pass) produces.
"""
import json
from types import SimpleNamespace

from core.pipeline.data.bulletin import pronunciation
from core.pipeline.data.bulletin.pronunciation import (
    apply_pronunciation_lexicon,
    normalise_for_tts,
)
from core.pipeline.data.bulletin.audio import tts_client
from core.pipeline.data.bulletin.audio.tts_client import compute_script_hash


# ── substitution correctness ─────────────────────────────────────────────────

def test_replaces_known_name():
    assert apply_pronunciation_lexicon("Nigel Farage spoke today.") == "Nigel Fa-rahzh spoke today."


def test_whole_word_only_not_substring():
    # "Farage" inside a longer word must NOT be touched (no word boundary).
    assert apply_pronunciation_lexicon("Faragex Farageism") == "Faragex Farageism"
    # but a real boundary (punctuation) IS a boundary.
    assert apply_pronunciation_lexicon("(Farage)") == "(Fa-rahzh)"


def test_case_sensitive():
    # lowercase is a different token — proper-noun entries only match as written.
    assert apply_pronunciation_lexicon("farage") == "farage"
    assert apply_pronunciation_lexicon("FARAGE") == "FARAGE"


def test_multiple_and_adjacent_in_one_pass():
    out = apply_pronunciation_lexicon("Starmer met Sunak in Kyiv.")
    assert out == "Star-mer met Soo-nak in Keev."


def test_unknown_words_untouched():
    assert apply_pronunciation_lexicon("The committee met.") == "The committee met."


def test_longer_key_wins_over_prefix(monkeypatch):
    # Rebuild the pattern from a lexicon where one key is a prefix of another.
    lex = {"Test": "AAA", "Testing": "BBB"}
    pat = pronunciation._build_lexicon_pattern(lex)
    monkeypatch.setattr(pronunciation, "PRONUNCIATION_LEXICON", lex)
    monkeypatch.setattr(pronunciation, "_LEXICON_PATTERN", pat)
    assert pronunciation.apply_pronunciation_lexicon("Testing Test") == "BBB AAA"


# ── cache-key invariant ───────────────────────────────────────────────────────

def test_normalise_for_tts_does_not_apply_lexicon():
    # The pre-hash pass must leave lexicon words untouched, or every lexicon edit
    # would re-hash (and re-bill) the affected segments.
    assert "Farage" in normalise_for_tts("Nigel Farage on 5%")
    assert "Fa-rahzh" not in normalise_for_tts("Nigel Farage on 5%")


def test_hash_is_over_pre_lexicon_text():
    # The hash a segment is cached under is computed on the normalised-but-not-yet-
    # spoken text; the spoken (lexicon-applied) form differs but never feeds the key.
    src = "Nigel Farage spoke."
    hashed_text = normalise_for_tts(src)
    spoken_text = apply_pronunciation_lexicon(hashed_text)
    assert hashed_text != spoken_text
    assert compute_script_hash(hashed_text) != compute_script_hash(spoken_text)
    # The cache key is the pre-lexicon hash — stable regardless of the lexicon.
    assert compute_script_hash(hashed_text) == compute_script_hash("Nigel Farage spoke.")


# ── the lexicon reaches the provider request (post-hash) ──────────────────────

def _fake_urlopen(captured):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"AUDIO"
    def _open(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return _Resp()
    return _open


def _stub_settings():
    return SimpleNamespace(
        elevenlabs_api_key="k", tts_stability=0.5, tts_similarity_boost=0.75,
        tts_style=0.15, tts_use_speaker_boost=True,
    )


def test_lexicon_applied_to_text_sent_to_provider(monkeypatch):
    captured = {}
    monkeypatch.setattr(tts_client, "settings", _stub_settings())
    monkeypatch.setattr(tts_client.urllib.request, "urlopen", _fake_urlopen(captured))
    tts_client.synthesize(
        "Nigel Farage spoke.", provider="elevenlabs", voice="v", model="m",
        audio_format="mp3_44100_128", previous_text="Before Sunak.", next_text="After.",
    )
    assert captured["body"]["text"] == "Nigel Fa-rahzh spoke."
    # neighbour context is substituted too, so prosody conditioning matches
    assert captured["body"]["previous_text"] == "Before Soo-nak."
