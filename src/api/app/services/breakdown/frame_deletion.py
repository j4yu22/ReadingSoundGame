from __future__ import annotations

import math
from dataclasses import dataclass

from app.services.breakdown.config import (
    DEFAULT_DELETION_FRAME_ALIGNMENT_CONFIG,
    DEFAULT_FEATURE_CONFIG,
    DeletionFrameAlignmentConfig,
    FeatureConfig,
)
from app.services.breakdown.features import extract_features, feature_distance, standardize_pair
from app.services.breakdown.models import ActivityRegion, AudioData, FeatureTrack


@dataclass(frozen=True)
class AlignStep:
    kind: str
    original_frame: int | None
    answer_frame: int | None
    distance: float


def frame_span_to_samples(
    track: FeatureTrack,
    start_frame: int | None,
    end_frame: int | None,
) -> tuple[int | None, int | None]:
    if start_frame is None or end_frame is None or not track.frame_starts:
        return None, None

    bounded_start = max(0, min(start_frame, len(track.frame_starts) - 1))
    bounded_end = max(bounded_start, min(end_frame, len(track.frame_starts) - 1))
    return (
        track.frame_starts[bounded_start],
        track.frame_starts[bounded_end] + track.frame_length,
    )


def subsequence_alignment(
    original_features: list[list[float]],
    answer_features: list[list[float]],
    config: DeletionFrameAlignmentConfig,
) -> list[AlignStep]:
    original_count = len(original_features)
    answer_count = len(answer_features)

    if not original_count or not answer_count:
        return []

    states = ("match", "delete", "answer_extra")
    costs = {
        state: [
            [math.inf for _ in range(answer_count + 1)]
            for _ in range(original_count + 1)
        ]
        for state in states
    }
    back: dict[str, list[list[tuple[str, int, int] | None]]] = {
        state: [
            [None for _ in range(answer_count + 1)]
            for _ in range(original_count + 1)
        ]
        for state in states
    }
    costs["match"][0][0] = 0.0

    for original_index in range(1, original_count + 1):
        if original_index == 1:
            costs["delete"][original_index][0] = (
                config.original_gap_open_penalty + config.original_gap_extend_penalty
            )
            back["delete"][original_index][0] = ("match", 0, 0)
            continue

        costs["delete"][original_index][0] = (
            costs["delete"][original_index - 1][0]
            + config.original_gap_extend_penalty
        )
        back["delete"][original_index][0] = ("delete", original_index - 1, 0)

    for answer_index in range(1, answer_count + 1):
        if answer_index == 1:
            costs["answer_extra"][0][answer_index] = (
                config.answer_gap_open_penalty + config.answer_gap_extend_penalty
            )
            back["answer_extra"][0][answer_index] = ("match", 0, 0)
            continue

        costs["answer_extra"][0][answer_index] = (
            costs["answer_extra"][0][answer_index - 1]
            + config.answer_gap_extend_penalty
        )
        back["answer_extra"][0][answer_index] = (
            "answer_extra",
            0,
            answer_index - 1,
        )

    for original_index in range(1, original_count + 1):
        for answer_index in range(1, answer_count + 1):
            distance = feature_distance(
                original_features[original_index - 1],
                answer_features[answer_index - 1],
            )
            previous_match = min(
                (
                    (costs[state][original_index - 1][answer_index - 1], state)
                    for state in states
                ),
                key=lambda item: item[0],
            )
            costs["match"][original_index][answer_index] = previous_match[0] + distance
            back["match"][original_index][answer_index] = (
                previous_match[1],
                original_index - 1,
                answer_index - 1,
            )

            delete_options = [
                (
                    costs["match"][original_index - 1][answer_index]
                    + config.original_gap_open_penalty
                    + config.original_gap_extend_penalty,
                    "match",
                ),
                (
                    costs["delete"][original_index - 1][answer_index]
                    + config.original_gap_extend_penalty,
                    "delete",
                ),
                (
                    costs["answer_extra"][original_index - 1][answer_index]
                    + config.original_gap_open_penalty
                    + config.original_gap_extend_penalty,
                    "answer_extra",
                ),
            ]
            previous_delete = min(delete_options, key=lambda item: item[0])
            costs["delete"][original_index][answer_index] = previous_delete[0]
            back["delete"][original_index][answer_index] = (
                previous_delete[1],
                original_index - 1,
                answer_index,
            )

            answer_extra_options = [
                (
                    costs["match"][original_index][answer_index - 1]
                    + config.answer_gap_open_penalty
                    + config.answer_gap_extend_penalty,
                    "match",
                ),
                (
                    costs["answer_extra"][original_index][answer_index - 1]
                    + config.answer_gap_extend_penalty,
                    "answer_extra",
                ),
                (
                    costs["delete"][original_index][answer_index - 1]
                    + config.answer_gap_open_penalty
                    + config.answer_gap_extend_penalty,
                    "delete",
                ),
            ]
            previous_answer_extra = min(answer_extra_options, key=lambda item: item[0])
            costs["answer_extra"][original_index][answer_index] = previous_answer_extra[0]
            back["answer_extra"][original_index][answer_index] = (
                previous_answer_extra[1],
                original_index,
                answer_index - 1,
            )

    state = min(
        states,
        key=lambda candidate: costs[candidate][original_count][answer_count],
    )
    steps: list[AlignStep] = []
    original_index = original_count
    answer_index = answer_count

    while original_index > 0 or answer_index > 0:
        previous = back[state][original_index][answer_index]

        if previous is None:
            break

        previous_state, previous_original, previous_answer = previous

        if state == "match":
            steps.append(
                AlignStep(
                    kind="match",
                    original_frame=original_index - 1,
                    answer_frame=answer_index - 1,
                    distance=feature_distance(
                        original_features[original_index - 1],
                        answer_features[answer_index - 1],
                    ),
                )
            )
        elif state == "delete":
            steps.append(
                AlignStep(
                    kind="delete",
                    original_frame=original_index - 1,
                    answer_frame=None,
                    distance=0.0,
                )
            )
        elif state == "answer_extra":
            steps.append(
                AlignStep(
                    kind="answer_extra",
                    original_frame=None,
                    answer_frame=answer_index - 1,
                    distance=0.0,
                )
            )

        state = previous_state
        original_index, answer_index = previous_original, previous_answer

    return list(reversed(steps))


def step_role(step: AlignStep) -> str:
    return "deletion" if step.kind == "delete" else "match"


def group_role(group: list[AlignStep]) -> str:
    return step_role(group[0])


def frame_span_duration_seconds(frames: list[int | None], track: FeatureTrack, audio: AudioData) -> float:
    present_frames = [frame for frame in frames if frame is not None]

    if not present_frames:
        return 0.0

    hop_seconds = track.hop_length / audio.sample_rate
    return (max(present_frames) - min(present_frames) + 1) * hop_seconds


def group_duration_seconds(
    group: list[AlignStep],
    original_track: FeatureTrack,
    answer_track: FeatureTrack,
    original_audio: AudioData,
    answer_audio: AudioData,
) -> float:
    return max(
        frame_span_duration_seconds(
            [step.original_frame for step in group],
            original_track,
            original_audio,
        ),
        frame_span_duration_seconds(
            [step.answer_frame for step in group],
            answer_track,
            answer_audio,
        ),
    )


def group_alignment_steps(steps: list[AlignStep]) -> list[list[AlignStep]]:
    groups: list[list[AlignStep]] = []

    for step in steps:
        role = step_role(step)

        if groups and role == group_role(groups[-1]):
            groups[-1].append(step)
            continue

        groups.append([step])

    return groups


def compact_groups(
    groups: list[list[AlignStep]],
    original_track: FeatureTrack,
    answer_track: FeatureTrack,
    original_audio: AudioData,
    answer_audio: AudioData,
    config: DeletionFrameAlignmentConfig,
) -> list[list[AlignStep]]:
    compacted: list[list[AlignStep]] = []

    for group in groups:
        role = group_role(group)
        duration = group_duration_seconds(
            group,
            original_track,
            answer_track,
            original_audio,
            answer_audio,
        )
        minimum = (
            config.minimum_match_seconds
            if role == "match"
            else config.minimum_deletion_seconds
        )

        if duration >= minimum or not compacted:
            compacted.append(group)
            continue

        compacted[-1].extend(group)

    merged: list[list[AlignStep]] = []
    index = 0

    while index < len(compacted):
        group = compacted[index]

        if (
            0 < index < len(compacted) - 1
            and group_role(group) == "match"
            and group_duration_seconds(
                group,
                original_track,
                answer_track,
                original_audio,
                answer_audio,
            )
            <= config.max_match_gap_to_merge_deletions_seconds
            and group_role(merged[-1]) == "deletion"
            and group_role(compacted[index + 1]) == "deletion"
        ):
            merged[-1].extend(group)
            merged[-1].extend(compacted[index + 1])
            index += 2
            continue

        if merged and group_role(merged[-1]) == group_role(group):
            merged[-1].extend(group)
        else:
            merged.append(group)

        index += 1

    if (
        len(merged) > 1
        and group_role(merged[0]) == "match"
        and group_role(merged[1]) == "deletion"
        and group_duration_seconds(
            merged[0],
            original_track,
            answer_track,
            original_audio,
            answer_audio,
        )
        <= config.max_edge_match_to_merge_deletion_seconds
    ):
        merged[1].extend(merged[0])
        merged = merged[1:]

    if (
        len(merged) > 1
        and group_role(merged[-1]) == "match"
        and group_role(merged[-2]) == "deletion"
        and group_duration_seconds(
            merged[-1],
            original_track,
            answer_track,
            original_audio,
            answer_audio,
        )
        <= config.max_edge_match_to_merge_deletion_seconds
    ):
        merged[-2].extend(merged[-1])
        merged = merged[:-1]

    return merged


def frames_for_group(group: list[AlignStep]) -> tuple[int | None, int | None, int | None, int | None]:
    original_frames = [step.original_frame for step in group if step.original_frame is not None]
    answer_frames = [step.answer_frame for step in group if step.answer_frame is not None]
    return (
        min(original_frames) if original_frames else None,
        max(original_frames) if original_frames else None,
        min(answer_frames) if answer_frames else None,
        max(answer_frames) if answer_frames else None,
    )


def mean_similarity(group: list[AlignStep]) -> float:
    distances = [step.distance for step in group if step.kind == "match"]

    if not distances:
        return 0.0

    return 1.0 / (1.0 + (sum(distances) / len(distances)))


def build_regions(
    steps: list[AlignStep],
    original_track: FeatureTrack,
    answer_track: FeatureTrack,
    original_audio: AudioData,
    answer_audio: AudioData,
    config: DeletionFrameAlignmentConfig,
) -> list[ActivityRegion]:
    groups = compact_groups(
        group_alignment_steps(steps),
        original_track,
        answer_track,
        original_audio,
        answer_audio,
        config,
    )
    regions: list[ActivityRegion] = []
    pad_original = int(original_audio.sample_rate * config.deletion_padding_seconds)
    sound_number = 1

    for group in groups:
        original_frame_start, original_frame_end, answer_frame_start, answer_frame_end = (
            frames_for_group(group)
        )
        original_start, original_end = frame_span_to_samples(
            original_track,
            original_frame_start,
            original_frame_end,
        )
        answer_start, answer_end = frame_span_to_samples(
            answer_track,
            answer_frame_start,
            answer_frame_end,
        )
        role = "deletion" if original_start is not None and answer_start is None else "match"

        if role == "deletion" and original_start is not None and original_end is not None:
            original_start = max(0, original_start - pad_original)
            original_end = min(len(original_audio.samples), original_end + pad_original)

        regions.append(
            ActivityRegion(
                name=f"sound{sound_number}",
                role=role,
                original_start=original_start,
                original_end=original_end,
                answer_start=answer_start,
                answer_end=answer_end,
                similarity=mean_similarity(group),
                original_sound_labels=(f"{sound_number}.1",) if original_start is not None else (),
                answer_sound_labels=(f"{sound_number}.2",) if answer_start is not None else (),
            )
        )
        sound_number += 1

    return regions


def analyze_audio(
    original_audio: AudioData,
    answer_audio: AudioData,
    feature_config: FeatureConfig = DEFAULT_FEATURE_CONFIG,
    config: DeletionFrameAlignmentConfig = DEFAULT_DELETION_FRAME_ALIGNMENT_CONFIG,
) -> list[ActivityRegion]:
    original_track = extract_features(original_audio, feature_config)
    answer_track = extract_features(answer_audio, feature_config)
    original_features, answer_features = standardize_pair(
        original_track.features,
        answer_track.features,
    )
    return build_regions(
        subsequence_alignment(original_features, answer_features, config),
        original_track,
        answer_track,
        original_audio,
        answer_audio,
        config,
    )
