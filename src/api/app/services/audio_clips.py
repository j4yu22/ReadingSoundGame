from __future__ import annotations

import hashlib
import sys
import wave
from array import array
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from app.core.config import API_DIR, settings
from app.services.text_to_speech import DialogueError, load_dialogue, wrap_ssml


TICKS_PER_SECOND = 10_000_000
CLIP_LEAD_SECONDS = 0.035
CLIP_TAIL_SECONDS = 0.2
CLIP_NEXT_GUARD_SECONDS = 0.025
CACHE_VERSION = "v5-carrier-phrase"
CACHE_DIR = API_DIR / ".cache" / "speech-clips"
TRIM_WINDOW_SECONDS = 0.006
TRIM_HEADROOM_SECONDS = 0.04
TRIM_THRESHOLD_RATIO = 0.015
TRIM_MIN_THRESHOLD = 260
MIN_CLIP_SECONDS = 0.36


@dataclass(frozen=True)
class WordBoundary:
    text: str
    start_seconds: float
    duration_seconds: float


def normalize_audio_word(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def ticks_to_seconds(value: Any) -> float:
    if value is None:
        return 0.0

    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())

    try:
        return float(value) / TICKS_PER_SECOND
    except (TypeError, ValueError):
        return 0.0


def build_source_tokens(tokens: list[str], fallback_phrase: str = "") -> list[str]:
    clean_tokens = [token.strip() for token in tokens if token.strip()]

    if clean_tokens:
        return clean_tokens

    return [token.strip() for token in fallback_phrase.split() if token.strip()]


def build_source_phrase(tokens: list[str], fallback_phrase: str = "") -> str:
    return " ".join(build_source_tokens(tokens, fallback_phrase))


def build_clip_ssml_from_fragment(fragment: str) -> str:
    dialogue = load_dialogue()
    lines = dialogue.get("lines", {})
    line = lines.get("token_sound") if isinstance(lines, dict) else None

    if not isinstance(line, dict):
        line = {}

    return wrap_ssml(fragment, dialogue, line)


def build_clip_ssml(source_tokens: list[str]) -> str:
    fragment = ". ".join(escape(token) for token in source_tokens)

    return build_clip_ssml_from_fragment(f"{fragment}.")


def build_carrier_clip_ssml(source_phrase: str) -> str:
    phrase = source_phrase.strip()

    if not phrase:
        raise DialogueError("No source phrase was provided for clipping.")

    if phrase[-1] not in ".!?":
        phrase = f"{phrase}."

    return build_clip_ssml_from_fragment(escape(phrase))


def cache_path_for_clip(ssml: str, target_word: str, occurrence: int) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(
        f"{CACHE_VERSION}\n{ssml}\n{target_word}\n{occurrence}".encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / f"{cache_key}.wav"


def synthesize_wav_with_boundaries(ssml: str) -> tuple[bytes, list[WordBoundary]]:
    if not settings.tts_ready:
        raise DialogueError(
            "Azure speech is not configured. Set AZURE_SPEECH_KEY and "
            "AZURE_SPEECH_REGION in src/api/.env or test/arthur/.env."
        )

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:
        raise DialogueError(
            "Azure Speech SDK is not installed. Run `uv sync` in src/api."
        ) from exc

    boundaries: list[WordBoundary] = []
    speech_config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key,
        region=settings.azure_speech_region,
    )
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )

    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=None,
    )

    def on_word_boundary(event: speechsdk.SessionEventArgs) -> None:
        text = str(getattr(event, "text", "") or "").strip()

        if not normalize_audio_word(text):
            return

        boundaries.append(
            WordBoundary(
                text=text,
                start_seconds=ticks_to_seconds(getattr(event, "audio_offset", 0)),
                duration_seconds=ticks_to_seconds(getattr(event, "duration", 0)),
            )
        )

    synthesizer.synthesis_word_boundary.connect(on_word_boundary)
    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        raise DialogueError(
            details.error_details
            or f"Azure clip synthesis canceled: {details.reason}"
        )

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise DialogueError(f"Azure clip synthesis failed: {result.reason}")

    return bytes(result.audio_data), boundaries


def find_word_boundary_span(
    boundaries: list[WordBoundary],
    target_word: str,
    occurrence: int,
) -> tuple[WordBoundary, WordBoundary | None]:
    normalized_target = normalize_audio_word(target_word)
    matches = [
        (index, boundary)
        for index, boundary in enumerate(boundaries)
        if normalize_audio_word(boundary.text) == normalized_target
    ]

    if not normalized_target:
        raise DialogueError("No target word was provided for clipping.")

    if not matches:
        available = ", ".join(boundary.text for boundary in boundaries) or "none"
        raise DialogueError(
            f'Could not find "{target_word}" in synthesized phrase. '
            f"Azure reported: {available}."
        )

    index = len(matches) - 1 if occurrence < 0 else min(occurrence, len(matches) - 1)
    boundary_index, boundary = matches[index]
    next_boundary = (
        boundaries[boundary_index + 1]
        if boundary_index + 1 < len(boundaries)
        else None
    )
    return boundary, next_boundary


def get_frame_amplitudes(
    frames: bytes,
    sample_width: int,
    channels: int,
) -> list[int]:
    if sample_width != 2 or channels < 1:
        return []

    samples = array("h")
    samples.frombytes(frames)

    if sys.byteorder == "big":
        samples.byteswap()

    return [
        max(abs(sample) for sample in samples[index:index + channels])
        for index in range(0, len(samples), channels)
    ]


def average_window(amplitudes: list[int], start: int, window_size: int) -> float:
    window = amplitudes[start:start + window_size]

    if not window:
        return 0.0

    return sum(window) / len(window)


def find_active_frame_range(
    amplitudes: list[int],
    frame_rate: int,
) -> tuple[int, int]:
    if not amplitudes:
        return 0, 0

    max_amplitude = max(amplitudes)

    if max_amplitude <= 0:
        return 0, len(amplitudes)

    threshold = max(TRIM_MIN_THRESHOLD, int(max_amplitude * TRIM_THRESHOLD_RATIO))
    window_size = max(1, int(frame_rate * TRIM_WINDOW_SECONDS))
    headroom = max(1, int(frame_rate * TRIM_HEADROOM_SECONDS))
    start = 0
    end = len(amplitudes)

    for index in range(0, len(amplitudes), window_size):
        if average_window(amplitudes, index, window_size) >= threshold:
            start = max(0, index - headroom)
            break

    for index in range(len(amplitudes) - window_size, -1, -window_size):
        if average_window(amplitudes, index, window_size) >= threshold:
            end = min(len(amplitudes), index + window_size + headroom)
            break

    minimum_frames = int(frame_rate * MIN_CLIP_SECONDS)

    if end - start < minimum_frames:
        center = (start + end) // 2
        start = max(0, center - minimum_frames // 2)
        end = min(len(amplitudes), start + minimum_frames)

    return start, max(start + 1, end)


def trim_quiet_edges(
    frames: bytes,
    sample_width: int,
    channels: int,
    frame_rate: int,
) -> bytes:
    amplitudes = get_frame_amplitudes(frames, sample_width, channels)

    if not amplitudes:
        return frames

    start_frame, end_frame = find_active_frame_range(amplitudes, frame_rate)
    bytes_per_frame = sample_width * channels
    return frames[start_frame * bytes_per_frame:end_frame * bytes_per_frame]


def clip_wav_audio(
    wav_audio: bytes,
    boundary: WordBoundary,
    next_boundary: WordBoundary | None,
) -> bytes:
    with wave.open(BytesIO(wav_audio), "rb") as source:
        frame_rate = source.getframerate()
        total_frames = source.getnframes()
        parameters = source.getparams()
        sample_width = source.getsampwidth()
        channels = source.getnchannels()

        duration_seconds = boundary.duration_seconds

        if duration_seconds <= 0:
            duration_seconds = 0.45

        start_seconds = max(0.0, boundary.start_seconds - CLIP_LEAD_SECONDS)
        end_seconds = min(
            total_frames / frame_rate,
            boundary.start_seconds + duration_seconds + CLIP_TAIL_SECONDS,
        )

        if next_boundary:
            end_seconds = min(
                end_seconds,
                max(start_seconds, next_boundary.start_seconds - CLIP_NEXT_GUARD_SECONDS),
            )

        start_frame = int(start_seconds * frame_rate)
        frame_count = max(1, int((end_seconds - start_seconds) * frame_rate))

        source.setpos(min(start_frame, total_frames))
        frames = source.readframes(frame_count)
        frames = trim_quiet_edges(frames, sample_width, channels, frame_rate)

    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setparams(parameters)
        target.writeframes(frames)

    return output.getvalue()


def synthesize_token_clip(
    source_tokens: list[str],
    target_word: str,
    occurrence: int = 0,
    source_phrase: str = "",
) -> bytes:
    if not source_tokens and not source_phrase.strip():
        raise DialogueError("No source phrase was provided for clipping.")

    ssml = (
        build_carrier_clip_ssml(source_phrase)
        if source_phrase.strip()
        else build_clip_ssml(source_tokens)
    )
    cache_path = cache_path_for_clip(ssml, target_word, occurrence)

    if cache_path.is_file():
        return cache_path.read_bytes()

    wav_audio, boundaries = synthesize_wav_with_boundaries(ssml)
    boundary, next_boundary = find_word_boundary_span(boundaries, target_word, occurrence)
    clip = clip_wav_audio(wav_audio, boundary, next_boundary)
    cache_path.write_bytes(clip)
    return clip
