from __future__ import annotations

from dataclasses import replace

from app.services.breakdown.config import DEFAULT_MATCHING_CONFIG
from app.services.breakdown import frame_deletion
from app.services.breakdown.models import ActivityRegion, AudioData
from app.services.breakdown.pipeline import analyze_pair


DELETION_MATCHING_CONFIG = replace(
    DEFAULT_MATCHING_CONFIG,
    minimum_match_similarity=0.65,
)


def analyze_audio(original_audio: AudioData, answer_audio: AudioData) -> list[ActivityRegion]:
    regions = analyze_pair(
        original_audio,
        answer_audio,
        "deletion",
        matching_config=DELETION_MATCHING_CONFIG,
    ).regions

    if has_expected_deletion_layout(original_audio.label, answer_audio.label, regions):
        return regions

    frame_regions = frame_deletion.analyze_audio(original_audio, answer_audio)

    if has_expected_deletion_layout(original_audio.label, answer_audio.label, frame_regions):
        return frame_regions

    return infer_regions_from_word_shape(original_audio, answer_audio)


def has_usable_deletion(regions: list[ActivityRegion]) -> bool:
    return len(regions) >= 2 and any(region.role == "deletion" for region in regions)


def deletion_position(original: str, answer: str) -> tuple[str, int, int]:
    original_text = original.strip().lower()
    answer_text = answer.strip().lower()
    prefix = 0

    while (
        prefix < len(original_text)
        and prefix < len(answer_text)
        and original_text[prefix] == answer_text[prefix]
    ):
        prefix += 1

    suffix = 0
    original_remaining = len(original_text) - prefix
    answer_remaining = len(answer_text) - prefix

    while (
        suffix < original_remaining
        and suffix < answer_remaining
        and original_text[len(original_text) - suffix - 1]
        == answer_text[len(answer_text) - suffix - 1]
    ):
        suffix += 1

    if prefix == 0:
        return "start", prefix, suffix

    if suffix == 0:
        return "end", prefix, suffix

    return "middle", prefix, suffix


def has_expected_deletion_layout(
    original: str,
    answer: str,
    regions: list[ActivityRegion],
) -> bool:
    if not has_usable_deletion(regions):
        return False

    position, _, _ = deletion_position(original, answer)
    deletion_indexes = [
        index
        for index, region in enumerate(regions)
        if region.role == "deletion"
    ]

    if position == "start":
        return any(index == 0 for index in deletion_indexes)

    if position == "end":
        return any(index == len(regions) - 1 for index in deletion_indexes)

    return any(0 < index < len(regions) - 1 for index in deletion_indexes)


def proportional_sample(audio: AudioData, character_index: int, character_count: int) -> int:
    if character_count <= 0:
        return audio.trim_start

    span = max(0, audio.trim_end - audio.trim_start)
    ratio = max(0.0, min(1.0, character_index / character_count))
    return audio.trim_start + round(span * ratio)


def bounded_deletion_span(
    audio: AudioData,
    start: int,
    end: int,
) -> tuple[int, int]:
    minimum_samples = int(audio.sample_rate * 0.06)
    start = max(audio.trim_start, min(start, audio.trim_end))
    end = max(start, min(end, audio.trim_end))

    if end - start >= minimum_samples:
        return start, end

    center = (start + end) // 2
    half = minimum_samples // 2
    return (
        max(audio.trim_start, center - half),
        min(audio.trim_end, center + half),
    )


def infer_regions_from_word_shape(
    original_audio: AudioData,
    answer_audio: AudioData,
) -> list[ActivityRegion]:
    _, prefix, suffix = deletion_position(original_audio.label, answer_audio.label)
    original_length = max(1, len(original_audio.label.strip()))
    answer_length = max(1, len(answer_audio.label.strip()))
    original_delete_start = proportional_sample(original_audio, prefix, original_length)
    original_delete_end = proportional_sample(
        original_audio,
        original_length - suffix,
        original_length,
    )
    original_delete_start, original_delete_end = bounded_deletion_span(
        original_audio,
        original_delete_start,
        original_delete_end,
    )
    answer_prefix_end = proportional_sample(answer_audio, prefix, answer_length)
    answer_suffix_start = proportional_sample(answer_audio, answer_length - suffix, answer_length)
    regions: list[ActivityRegion] = []

    if original_delete_start > original_audio.trim_start:
        regions.append(
            ActivityRegion(
                name=f"sound{len(regions) + 1}",
                role="match",
                original_start=original_audio.trim_start,
                original_end=original_delete_start,
                answer_start=answer_audio.trim_start,
                answer_end=answer_prefix_end,
                similarity=1.0,
                original_sound_labels=(f"{len(regions) + 1}.1",),
                answer_sound_labels=(f"{len(regions) + 1}.2",),
            )
        )

    regions.append(
        ActivityRegion(
            name=f"sound{len(regions) + 1}",
            role="deletion",
            original_start=original_delete_start,
            original_end=original_delete_end,
            answer_start=None,
            answer_end=None,
            similarity=0.0,
            original_sound_labels=(f"{len(regions) + 1}.1",),
            answer_sound_labels=(),
        )
    )

    if original_delete_end < original_audio.trim_end:
        regions.append(
            ActivityRegion(
                name=f"sound{len(regions) + 1}",
                role="match",
                original_start=original_delete_end,
                original_end=original_audio.trim_end,
                answer_start=answer_suffix_start,
                answer_end=answer_audio.trim_end,
                similarity=1.0,
                original_sound_labels=(f"{len(regions) + 1}.1",),
                answer_sound_labels=(f"{len(regions) + 1}.2",),
            )
        )

    return regions


def identify_delete_sound(regions: list[ActivityRegion]) -> str:
    for region in regions:
        if region.role == "deletion":
            return region.delete_sound

    return ""
