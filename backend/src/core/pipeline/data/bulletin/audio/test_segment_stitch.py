"""Prosody-stitch wiring: correct neighbour text per segment + it reaches the request."""
import json
from types import SimpleNamespace

from core.pipeline.data.bulletin.audio import tts_client
from core.pipeline.data.bulletin.audio.segment_synth import _compute_neighbours


# ── neighbour computation ────────────────────────────────────────────────────

def test_neighbours_basic_order():
    n = _compute_neighbours([{"text": "A"}, {"text": "B"}, {"text": "C"}])
    assert n[0] == (None, "B")   # first: no previous
    assert n[1] == ("A", "C")    # middle: both
    assert n[2] == ("B", None)   # last: no next


def test_neighbours_skip_empty_middle():
    # an empty (pause) segment is neither a key nor a neighbour — stitch to what's heard
    n = _compute_neighbours([{"text": "A"}, {"text": ""}, {"text": "C"}])
    assert 1 not in n
    assert n[0] == (None, "C")
    assert n[2] == ("A", None)


def test_neighbours_single_segment_has_no_context():
    assert _compute_neighbours([{"text": "only"}]) == {0: (None, None)}


def test_neighbours_all_empty_is_empty():
    assert _compute_neighbours([{"text": ""}, {"text": "   "}]) == {}


# ── the neighbour text reaches the ElevenLabs request body ───────────────────

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


def test_request_includes_neighbours_when_present(monkeypatch):
    captured = {}
    monkeypatch.setattr(tts_client, "settings", _stub_settings())
    monkeypatch.setattr(tts_client.urllib.request, "urlopen", _fake_urlopen(captured))
    out = tts_client.synthesize(
        "hello", provider="elevenlabs", voice="v", model="m",
        audio_format="mp3_44100_128", previous_text="PREV", next_text="NEXT",
    )
    assert out == b"AUDIO"
    assert captured["body"]["previous_text"] == "PREV"
    assert captured["body"]["next_text"] == "NEXT"


def test_request_omits_neighbours_on_edges(monkeypatch):
    captured = {}
    monkeypatch.setattr(tts_client, "settings", _stub_settings())
    monkeypatch.setattr(tts_client.urllib.request, "urlopen", _fake_urlopen(captured))
    tts_client.synthesize(
        "hello", provider="elevenlabs", voice="v", model="m",
        audio_format="mp3_44100_128",  # no neighbours (edge segment)
    )
    assert "previous_text" not in captured["body"]
    assert "next_text" not in captured["body"]
