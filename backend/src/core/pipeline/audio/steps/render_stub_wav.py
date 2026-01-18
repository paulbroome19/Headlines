from __future__ import annotations

import os
import wave


def estimate_duration_seconds(text: str) -> int:
    words = max(1, len((text or "").split()))
    seconds = int(max(5, words / 2.5))  # ~150 wpm
    return min(seconds, 20 * 60)


def write_silence_wav(path: str, seconds: int, sample_rate: int = 16000) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)

    n_channels = 1
    sampwidth = 2  # 16-bit PCM
    n_frames = seconds * sample_rate

    with wave.open(path, "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)