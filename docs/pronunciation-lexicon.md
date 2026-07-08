# Pronunciation lexicon

ElevenLabs sometimes mispronounces a name (e.g. "Farage"). We fix these with a
small **written form → spoken respelling** lexicon applied at synthesis time.

- **File:** `backend/src/core/pipeline/data/bulletin/pronunciation.py` →
  `PRONUNCIATION_LEXICON`.
- **Matching:** whole-word (`\b…\b`, never substrings), **case-sensitive**
  (entries matched exactly as written — this preserves case and avoids mangling
  lowercase homographs like "musk" the scent).
- **Applied where:** `tts_client.synthesize()`, **after** `script_hash` is
  computed. This is deliberate — see "Why it doesn't cost anything" below.

## Adding a name (when a tester reports one)

1. Render a couple of candidate respellings against the real voice and pick by
   ear. One-liner (uses the prod voice/model from `backend/.env`):

   ```bash
   cd backend
   poetry run python - <<'PY'
   import json, urllib.request, pathlib
   env = dict(l.split("=",1) for l in pathlib.Path(".env").read_text().splitlines()
              if "=" in l and not l.strip().startswith("#"))
   for spoken in ["Fa-rahj", "Fa-rahzh", "FARR-ahzh"]:      # <- your candidates
       body = {"text": f"Nigel {spoken} said today.", "model_id": env.get("TTS_MODEL","eleven_multilingual_v2"),
               "voice_settings": {"stability":0.5,"similarity_boost":0.75,"style":0.15,"use_speaker_boost":True}}
       url = f"https://api.elevenlabs.io/v1/text-to-speech/{env.get('TTS_VOICE','19STyYD15bswVz51nqLf')}?output_format=mp3_44100_128"
       req = urllib.request.Request(url, data=json.dumps(body).encode(),
             headers={"xi-api-key":env["ELEVENLABS_API_KEY"].strip(),"Content-Type":"application/json","Accept":"audio/mpeg"}, method="POST")
       pathlib.Path(f"/tmp/{spoken}.mp3").write_bytes(urllib.request.urlopen(req,timeout=60).read())
       print("wrote", f"/tmp/{spoken}.mp3")
   PY
   ```

   Respelling levers (no SSML — see below): hyphenate syllables (`Fa-rahj`),
   CAPS a stressed syllable (`FARR-ahzh`), spell vowels how they sound.

2. Add the entry to `PRONUNCIATION_LEXICON`, grouped with its neighbours:

   ```python
   "Farage": "Fa-rahj",
   ```

3. Ship. **No cache flush needed.**

## Why it doesn't cost anything

The lexicon is applied *after* the segment `script_hash` (the audio cache key) is
computed. So editing it never invalidates the cache: only **fresh** renders pick
up the new spelling; already-cached segments keep their old audio until the news
naturally turns the cache over. This is the whole reason it lives in the TTS
client and not in the pre-hash `normalise_for_tts` pass.

## Why respelling, not `<phoneme>` tags

Our model is `eleven_multilingual_v2`, which **does not support SSML `<phoneme>`
tags** (only Flash v2 / Turbo v2 / English v1 do). Plain respelling is the only
lever on this model. If we ever move to a phoneme-capable model, we can swap the
*values* to phoneme/IPA spellings while keeping the same lexicon interface.
