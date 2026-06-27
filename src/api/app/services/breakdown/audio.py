from __future__ import annotations

import re
import sys
import wave
from array import array
from io import BytesIO

from app.services.breakdown.config import (
    DEFAULT_AUDIO_LOAD_CONFIG,
    AudioLoadConfig,
)
from app.services.breakdown.models import AudioData
from app.services.text_to_speech import DialogueError


def clean_file_part(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    return clean or "word"


def read_wav_mono(wav_audio: bytes) -> tuple[int, list[float]]:
    with wave.open(BytesIO(wav_audio), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())

    if sample_width != 2:
        raise DialogueError(f"Expected 16-bit PCM WAV, got sample width {sample_width}.")

    pcm = array("h")
    pcm.frombytes(frames)

    if sys.byteorder == "big":
        pcm.byteswap()

    samples: list[float] = []

    for index in range(0, len(pcm), channels):
        samples.append(sum(pcm[index:index + channels]) / (channels * 32768.0))

    return sample_rate, samples


def write_wav_mono(sample_rate: int, samples: list[float]) -> bytes:
    pcm = array(
        "h",
        [
            max(-32768, min(32767, int(sample * 32767)))
            for sample in samples
        ],
    )
    output = BytesIO()

    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm.tobytes())

    return output.getvalue()


def trim_bounds(
    samples: list[float],
    sample_rate: int,
    config: AudioLoadConfig = DEFAULT_AUDIO_LOAD_CONFIG,
) -> tuple[int, int]:
    if not samples:
        return 0, 0

    peak = max(abs(sample) for sample in samples)

    if peak <= 0:
        return 0, len(samples)

    threshold = peak * config.silence_threshold_ratio
    start = 0
    end = len(samples)

    for index, sample in enumerate(samples):
        if abs(sample) >= threshold:
            start = index
            break

    for index in range(len(samples) - 1, -1, -1):
        if abs(samples[index]) >= threshold:
            end = index + 1
            break

    margin = int(sample_rate * config.silence_margin_seconds)
    return max(0, start - margin), min(len(samples), end + margin)


def load_audio(
    label: str,
    wav_audio: bytes,
    config: AudioLoadConfig = DEFAULT_AUDIO_LOAD_CONFIG,
) -> AudioData:
    sample_rate, samples = read_wav_mono(wav_audio)
    trim_start, trim_end = trim_bounds(samples, sample_rate, config)
    return AudioData(
        label=label,
        wav_bytes=wav_audio,
        sample_rate=sample_rate,
        samples=samples,
        trim_start=trim_start,
        trim_end=trim_end,
    )


def clip_samples(audio: AudioData, start: int | None, end: int | None) -> bytes:
    safe_start = max(0, min(start or 0, len(audio.samples)))
    safe_end = max(safe_start, min(end or 0, len(audio.samples)))
    return write_wav_mono(audio.sample_rate, audio.samples[safe_start:safe_end])
