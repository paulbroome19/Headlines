# Post-demo notes — deferred TTS/voice decisions

Parked during the demo voice work (PR #160). The demo voice is FROZEN after merge;
revisit these only with explicit instruction.

## Candidate TTS models (not adopted)

- **`eleven_v3`** — prosody upgrade (more expressive English). Costs pacing (~+17%
  duration: the sample ran 6.8 min vs 5.8) and gate latency, and switching model
  triggers a full `segment_audio` cache re-render (model is in the cache key). Revisit
  if flat delivery is the top complaint post-demo.
- **`eleven_flash_v2_5`** — latency play (~75 ms first-byte class), for the first-play
  gate. Fidelity trade vs `multilingual_v2`. Not a prosody upgrade.

## Cache key vs voice settings

`data.segment_audio` is uniquely keyed on `(script_hash, voice, model, audio_format)` —
**stability/style are NOT in the key**. Changing settings in prod would mix old-settings
cached segments with new-settings fresh ones in one bulletin (audibly inconsistent).
`script_hash = sha256(segment_text)` is also the iOS `/readiness` contract, so settings
must NOT be folded into it. If prod TTS settings ever start to change (per-env or repeated
tuning), add a **`voice_settings_hash` column to the unique key** (thread `voice_settings`
through `synthesize_segments` → repo) so old/new settings coexist. Not needed while
settings are fixed — a one-time cache flush covers a single change.

## Findings worth keeping (measured during PR #160)

- **`style` is a no-op** on `eleven_multilingual_v2` AND `eleven_turbo_v2_5` for our voice:
  style 0.15 vs 0.70 renders byte-identical at any fixed seed (verified, not a seed
  artifact). The only working `voice_settings` lever is **stability**. To get expressiveness
  beyond stability you need a different model (`eleven_v3`) or voice, not `style`.
- **Prosody stitching costs ~1.4s/segment** on `multilingual_v2` (measured, interleaved):
  greeting +1410 ms (+65%), opener +1393 ms (+19%). It is text-context, so segments stay
  parallel (no serialisation) — but each is +~1.4s. Hence stitching is applied to the
  background tail only; the first-play start-pack is unstitched by default
  (`segment_synth._STITCH_START_PACK = False`). Flip to stitch the open, accepting the cost.
