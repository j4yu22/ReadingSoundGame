from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from app.services.breakdown.models import ActivityRegion, AudioData
from app.services.speech_to_text import (
    apply_pronunciation_assessment,
    build_audio_config_from_wav_bytes,
    build_speech_config,
    cancellation_details,
    parse_json_result,
)
from app.services.text_to_speech import DialogueError


AZURE_TICKS_PER_SECOND = 10_000_000
PHONEME_ALIASES = {
    "g": "ɡ",
    "r": "ɹ",
}


@dataclass(frozen=True)
class TimedPhoneme:
    symbol: str
    start_sample: int
    end_sample: int


@dataclass(frozen=True)
class WordPhonemeBreakdown:
    word: str
    audio: AudioData
    phonemes: tuple[TimedPhoneme, ...]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(phoneme.symbol for phoneme in self.phonemes)


def strip_phoneme_wrappers(value: str) -> str:
    phoneme = value.strip()

    if len(phoneme) >= 2 and (
        (phoneme.startswith("/") and phoneme.endswith("/"))
        or (phoneme.startswith("[") and phoneme.endswith("]"))
    ):
        phoneme = phoneme[1:-1].strip()

    return phoneme


def normalize_phoneme(value: str) -> str:
    phoneme = unicodedata.normalize("NFC", strip_phoneme_wrappers(value))
    phoneme = phoneme.replace("ˈ", "").replace("ˌ", "").replace("ː", "")
    return PHONEME_ALIASES.get(phoneme, phoneme)


def parse_phoneme_sequence(value: Any, field_name: str) -> tuple[str, ...]:
    raw_values: list[str]

    if isinstance(value, str):
        stripped = strip_phoneme_wrappers(value)
        raw_values = stripped.split() if " " in stripped else [stripped]
    elif isinstance(value, list | tuple):
        raw_values = [str(item) for item in value]
    else:
        raw_values = []

    phonemes = tuple(
        strip_phoneme_wrappers(item)
        for item in raw_values
        if strip_phoneme_wrappers(item)
    )

    if not phonemes:
        raise DialogueError(
            f"Activity field `{field_name}` must contain one or more IPA phonemes."
        )

    return phonemes


def format_phonemes(phonemes: tuple[str, ...] | list[str]) -> str:
    if not phonemes:
        return ""
    return f"/{' '.join(phonemes)}/"


def numeric_ticks(value: Any) -> int | None:
    if isinstance(value, int | float):
        return max(0, round(value))
    return None


def ticks_to_sample(ticks: int, sample_rate: int) -> int:
    return round((ticks / AZURE_TICKS_PER_SECOND) * sample_rate)


def raw_phoneme_nodes(details: dict[str, Any]) -> list[dict[str, Any]]:
    nbest = details.get("NBest")

    if not isinstance(nbest, list) or not nbest or not isinstance(nbest[0], dict):
        return []

    words = nbest[0].get("Words")

    if not isinstance(words, list):
        return []

    nodes: list[dict[str, Any]] = []

    for word in words:
        if not isinstance(word, dict):
            continue

        phonemes = word.get("Phonemes")

        if not isinstance(phonemes, list):
            continue

        nodes.extend(node for node in phonemes if isinstance(node, dict))

    return nodes


def timed_phonemes_from_details(
    details: dict[str, Any],
    audio: AudioData,
) -> tuple[TimedPhoneme, ...]:
    timed: list[TimedPhoneme] = []
    sample_count = len(audio.samples)

    for node in raw_phoneme_nodes(details):
        symbol = str(node.get("Phoneme") or "").strip()
        offset = numeric_ticks(node.get("Offset"))
        duration = numeric_ticks(node.get("Duration"))

        if not symbol or offset is None or duration is None or duration <= 0:
            continue

        start = max(0, min(sample_count, ticks_to_sample(offset, audio.sample_rate)))
        end = max(
            start,
            min(
                sample_count,
                ticks_to_sample(offset + duration, audio.sample_rate),
            ),
        )

        if end <= start:
            continue

        timed.append(
            TimedPhoneme(
                symbol=unicodedata.normalize("NFC", symbol),
                start_sample=start,
                end_sample=end,
            )
        )

    return tuple(timed)


def assess_word_phonemes(word: str, audio: AudioData) -> WordPhonemeBreakdown:
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:
        raise DialogueError(
            "Azure Speech SDK is not installed. Run `uv sync` in src/api."
        ) from exc

    speech_config = build_speech_config(speechsdk)
    audio_config = build_audio_config_from_wav_bytes(speechsdk, audio.wav_bytes)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )
    apply_pronunciation_assessment(speechsdk, recognizer, word)
    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.Canceled:
        raise DialogueError(cancellation_details(speechsdk, result))

    if result.reason == speechsdk.ResultReason.NoMatch:
        raise DialogueError(f'Azure could not identify phonemes in "{word}".')

    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        raise DialogueError(
            f'Unexpected Azure phoneme result for "{word}": {result.reason}'
        )

    phonemes = timed_phonemes_from_details(parse_json_result(speechsdk, result), audio)

    if not phonemes:
        raise DialogueError(
            f'Azure returned no timed phonemes for "{word}". Check its spelling or pronunciation.'
        )

    return WordPhonemeBreakdown(word=word, audio=audio, phonemes=phonemes)


def normalized_symbols(word: WordPhonemeBreakdown) -> tuple[str, ...]:
    return tuple(normalize_phoneme(symbol) for symbol in word.symbols)


def normalized_hint(phonemes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(normalize_phoneme(symbol) for symbol in phonemes)


def infer_change_phonemes(
    original: WordPhonemeBreakdown,
    answer: WordPhonemeBreakdown,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    original_symbols = normalized_symbols(original)
    answer_symbols = normalized_symbols(answer)
    prefix_length = 0

    while (
        prefix_length < len(original_symbols)
        and prefix_length < len(answer_symbols)
        and original_symbols[prefix_length] == answer_symbols[prefix_length]
    ):
        prefix_length += 1

    suffix_length = 0
    while (
        suffix_length < len(original_symbols) - prefix_length
        and suffix_length < len(answer_symbols) - prefix_length
        and original_symbols[-(suffix_length + 1)] == answer_symbols[-(suffix_length + 1)]
    ):
        suffix_length += 1

    original_end = len(original_symbols) - suffix_length
    answer_end = len(answer_symbols) - suffix_length
    return (
        original.symbols[prefix_length:original_end],
        answer.symbols[prefix_length:answer_end],
    )


def infer_deletion_phonemes(
    original: WordPhonemeBreakdown,
    answer: WordPhonemeBreakdown,
) -> tuple[str, ...]:
    deleted, inserted = infer_change_phonemes(original, answer)

    if deleted and not inserted:
        return deleted

    raise DialogueError(
        f'Could not infer one contiguous deletion from "{original.word}" '
        f'{sequence_description(original.symbols)} to "{answer.word}" '
        f'{sequence_description(answer.symbols)}.'
    )


def infer_substitution_phonemes(
    original: WordPhonemeBreakdown,
    answer: WordPhonemeBreakdown,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    old_phonemes, new_phonemes = infer_change_phonemes(original, answer)

    if old_phonemes and new_phonemes:
        return old_phonemes, new_phonemes

    raise DialogueError(
        f'Could not infer one contiguous substitution from "{original.word}" '
        f'{sequence_description(original.symbols)} to "{answer.word}" '
        f'{sequence_description(answer.symbols)}.'
    )


def subsequence_starts(
    sequence: tuple[str, ...],
    target: tuple[str, ...],
) -> list[int]:
    if not target or len(target) > len(sequence):
        return []

    return [
        index
        for index in range(len(sequence) - len(target) + 1)
        if sequence[index : index + len(target)] == target
    ]


def sequence_description(sequence: tuple[str, ...]) -> str:
    return format_phonemes(list(sequence))


def locate_deletion(
    original: WordPhonemeBreakdown,
    answer: WordPhonemeBreakdown,
    deleted_phonemes: tuple[str, ...],
) -> tuple[int, int]:
    original_symbols = normalized_symbols(original)
    answer_symbols = normalized_symbols(answer)
    deleted = normalized_hint(deleted_phonemes)

    for original_start in subsequence_starts(original_symbols, deleted):
        remaining = (
            original_symbols[:original_start]
            + original_symbols[original_start + len(deleted) :]
        )

        if remaining == answer_symbols:
            return original_start, original_start

    raise DialogueError(
        f"Deletion hint {sequence_description(deleted_phonemes)} does not transform "
        f'"{original.word}" {sequence_description(original.symbols)} into '
        f'"{answer.word}" {sequence_description(answer.symbols)}.'
    )


def locate_substitution(
    original: WordPhonemeBreakdown,
    answer: WordPhonemeBreakdown,
    old_phonemes: tuple[str, ...],
    new_phonemes: tuple[str, ...],
) -> tuple[int, int]:
    original_symbols = normalized_symbols(original)
    answer_symbols = normalized_symbols(answer)
    old = normalized_hint(old_phonemes)
    new = normalized_hint(new_phonemes)

    for original_start in subsequence_starts(original_symbols, old):
        for answer_start in subsequence_starts(answer_symbols, new):
            prefix_matches = (
                original_symbols[:original_start] == answer_symbols[:answer_start]
            )
            suffix_matches = (
                original_symbols[original_start + len(old) :]
                == answer_symbols[answer_start + len(new) :]
            )

            if prefix_matches and suffix_matches:
                return original_start, answer_start

    raise DialogueError(
        f"Substitution hints {sequence_description(old_phonemes)} to "
        f"{sequence_description(new_phonemes)} do not transform "
        f'"{original.word}" {sequence_description(original.symbols)} into '
        f'"{answer.word}" {sequence_description(answer.symbols)}.'
    )


def phoneme_boundaries(word: WordPhonemeBreakdown) -> tuple[int, ...]:
    phonemes = word.phonemes

    if not phonemes:
        return ()

    audio = word.audio
    first = max(audio.trim_start, min(phonemes[0].start_sample, audio.trim_end))
    last = max(first, min(phonemes[-1].end_sample, audio.trim_end))

    if last - first < len(phonemes):
        first = max(audio.trim_start, min(first, last - len(phonemes)))

    boundaries = [first]

    for index, (left, right) in enumerate(zip(phonemes, phonemes[1:]), start=1):
        candidate = round((left.end_sample + right.start_sample) / 2)
        minimum = boundaries[-1] + 1
        remaining = len(phonemes) - index
        maximum = max(minimum, last - remaining)
        boundaries.append(max(minimum, min(candidate, maximum)))

    boundaries.append(last)
    return tuple(boundaries)


def phoneme_span(
    word: WordPhonemeBreakdown,
    start: int,
    end: int,
) -> tuple[int | None, int | None]:
    if start >= end:
        return None, None

    boundaries = phoneme_boundaries(word)

    if not boundaries or end >= len(boundaries):
        raise DialogueError(f'Invalid phoneme span for "{word.word}".')

    return boundaries[start], boundaries[end]


def append_region(
    regions: list[ActivityRegion],
    role: str,
    original: WordPhonemeBreakdown,
    answer: WordPhonemeBreakdown,
    original_range: tuple[int, int],
    answer_range: tuple[int, int],
) -> None:
    original_start, original_end = phoneme_span(original, *original_range)
    answer_start, answer_end = phoneme_span(answer, *answer_range)

    regions.append(
        ActivityRegion(
            name=f"sound{len(regions) + 1}",
            role=role,
            original_start=original_start,
            original_end=original_end,
            answer_start=answer_start,
            answer_end=answer_end,
            similarity=1.0 if role == "match" else 0.0,
            original_sound_labels=original.symbols[
                original_range[0] : original_range[1]
            ],
            answer_sound_labels=answer.symbols[answer_range[0] : answer_range[1]],
        )
    )


def build_deletion_regions(
    original: WordPhonemeBreakdown,
    answer: WordPhonemeBreakdown,
    deleted_phonemes: tuple[str, ...],
) -> list[ActivityRegion]:
    original_start, answer_start = locate_deletion(
        original,
        answer,
        deleted_phonemes,
    )
    change_end = original_start + len(deleted_phonemes)
    regions: list[ActivityRegion] = []

    if original_start:
        append_region(
            regions,
            "match",
            original,
            answer,
            (0, original_start),
            (0, answer_start),
        )

    append_region(
        regions,
        "deletion",
        original,
        answer,
        (original_start, change_end),
        (answer_start, answer_start),
    )

    if change_end < len(original.phonemes):
        append_region(
            regions,
            "match",
            original,
            answer,
            (change_end, len(original.phonemes)),
            (answer_start, len(answer.phonemes)),
        )

    return regions


def build_substitution_regions(
    original: WordPhonemeBreakdown,
    answer: WordPhonemeBreakdown,
    old_phonemes: tuple[str, ...],
    new_phonemes: tuple[str, ...],
) -> list[ActivityRegion]:
    original_start, answer_start = locate_substitution(
        original,
        answer,
        old_phonemes,
        new_phonemes,
    )
    original_change_end = original_start + len(old_phonemes)
    answer_change_end = answer_start + len(new_phonemes)
    regions: list[ActivityRegion] = []

    if original_start:
        append_region(
            regions,
            "match",
            original,
            answer,
            (0, original_start),
            (0, answer_start),
        )

    append_region(
        regions,
        "discrepancy",
        original,
        answer,
        (original_start, original_change_end),
        (answer_start, answer_change_end),
    )

    if original_change_end < len(original.phonemes):
        append_region(
            regions,
            "match",
            original,
            answer,
            (original_change_end, len(original.phonemes)),
            (answer_change_end, len(answer.phonemes)),
        )

    return regions


def marker_payload(word: WordPhonemeBreakdown) -> list[dict[str, int | str]]:
    boundaries = phoneme_boundaries(word)

    return [
        {
            "phoneme": phoneme.symbol,
            "startMs": round((boundaries[index] / word.audio.sample_rate) * 1000),
            "endMs": round((boundaries[index + 1] / word.audio.sample_rate) * 1000),
        }
        for index, phoneme in enumerate(word.phonemes)
    ]
