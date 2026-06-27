from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

from substitution_breakdown import (
    OUTPUT_DIR,
    AudioData,
    FeatureTrack,
    HOP_SECONDS,
    clean_file_part,
    clear_output_dir,
    clip_region,
    extract_features,
    feature_distance,
    load_audio,
    milliseconds,
    standardize_pair,
    synthesize_inputs,
)


ORIGINAL_GAP_OPEN_PENALTY = 1.2
ORIGINAL_GAP_EXTEND_PENALTY = 0.18
ANSWER_GAP_OPEN_PENALTY = 35.0
ANSWER_GAP_EXTEND_PENALTY = 1.0
MIN_DELETION_SECONDS = 0.012
MIN_MATCH_SECONDS = 0.025
DELETION_PAD_SECONDS = 0.02
MAX_MATCH_GAP_TO_MERGE_DELETIONS_SECONDS = 0.08
MAX_EDGE_MATCH_TO_MERGE_DELETION_SECONDS = 0.08


@dataclass(frozen=True)
class AlignStep:
    kind: str
    original_frame: int | None
    answer_frame: int | None
    distance: float


@dataclass(frozen=True)
class DeletionRegion:
    name: str
    role: str
    original_start: int | None
    original_end: int | None
    answer_start: int | None
    answer_end: int | None
    similarity: float


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
                ORIGINAL_GAP_OPEN_PENALTY + ORIGINAL_GAP_EXTEND_PENALTY
            )
            back["delete"][original_index][0] = ("match", 0, 0)
        else:
            costs["delete"][original_index][0] = (
                costs["delete"][original_index - 1][0]
                + ORIGINAL_GAP_EXTEND_PENALTY
            )
            back["delete"][original_index][0] = (
                "delete",
                original_index - 1,
                0,
            )

    for answer_index in range(1, answer_count + 1):
        if answer_index == 1:
            costs["answer_extra"][0][answer_index] = (
                ANSWER_GAP_OPEN_PENALTY + ANSWER_GAP_EXTEND_PENALTY
            )
            back["answer_extra"][0][answer_index] = ("match", 0, 0)
        else:
            costs["answer_extra"][0][answer_index] = (
                costs["answer_extra"][0][answer_index - 1]
                + ANSWER_GAP_EXTEND_PENALTY
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
                    (
                        costs[state][original_index - 1][answer_index - 1],
                        state,
                    )
                    for state in states
                ),
                key=lambda item: item[0],
            )
            costs["match"][original_index][answer_index] = (
                previous_match[0] + distance
            )
            back["match"][original_index][answer_index] = (
                previous_match[1],
                original_index - 1,
                answer_index - 1,
            )

            delete_options = [
                (
                    costs["match"][original_index - 1][answer_index]
                    + ORIGINAL_GAP_OPEN_PENALTY
                    + ORIGINAL_GAP_EXTEND_PENALTY,
                    "match",
                ),
                (
                    costs["delete"][original_index - 1][answer_index]
                    + ORIGINAL_GAP_EXTEND_PENALTY,
                    "delete",
                ),
                (
                    costs["answer_extra"][original_index - 1][answer_index]
                    + ORIGINAL_GAP_OPEN_PENALTY
                    + ORIGINAL_GAP_EXTEND_PENALTY,
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
                    + ANSWER_GAP_OPEN_PENALTY
                    + ANSWER_GAP_EXTEND_PENALTY,
                    "match",
                ),
                (
                    costs["answer_extra"][original_index][answer_index - 1]
                    + ANSWER_GAP_EXTEND_PENALTY,
                    "answer_extra",
                ),
                (
                    costs["delete"][original_index][answer_index - 1]
                    + ANSWER_GAP_OPEN_PENALTY
                    + ANSWER_GAP_EXTEND_PENALTY,
                    "delete",
                ),
            ]
            previous_answer_extra = min(
                answer_extra_options,
                key=lambda item: item[0],
            )
            costs["answer_extra"][original_index][answer_index] = (
                previous_answer_extra[0]
            )
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


def frame_span_duration_seconds(frames: list[int | None]) -> float:
    present_frames = [frame for frame in frames if frame is not None]

    if not present_frames:
        return 0.0

    return (max(present_frames) - min(present_frames) + 1) * HOP_SECONDS


def group_duration_seconds(group: list[AlignStep]) -> float:
    return max(
        frame_span_duration_seconds([step.original_frame for step in group]),
        frame_span_duration_seconds([step.answer_frame for step in group]),
    )


def group_alignment_steps(steps: list[AlignStep]) -> list[list[AlignStep]]:
    groups: list[list[AlignStep]] = []

    for step in steps:
        role = step_role(step)

        if groups:
            previous_role = group_role(groups[-1])

            if role == previous_role:
                groups[-1].append(step)
                continue

        groups.append([step])

    return groups


def compact_groups(
    groups: list[list[AlignStep]],
    original_track: FeatureTrack,
    answer_track: FeatureTrack,
) -> list[list[AlignStep]]:
    compacted: list[list[AlignStep]] = []

    for group in groups:
        role = group_role(group)
        duration = group_duration_seconds(group)
        minimum = MIN_MATCH_SECONDS if role == "match" else MIN_DELETION_SECONDS

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
            and group_duration_seconds(group)
            <= MAX_MATCH_GAP_TO_MERGE_DELETIONS_SECONDS
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
        and group_duration_seconds(merged[0])
        <= MAX_EDGE_MATCH_TO_MERGE_DELETION_SECONDS
    ):
        merged[1].extend(merged[0])
        merged = merged[1:]

    if (
        len(merged) > 1
        and group_role(merged[-1]) == "match"
        and group_role(merged[-2]) == "deletion"
        and group_duration_seconds(merged[-1])
        <= MAX_EDGE_MATCH_TO_MERGE_DELETION_SECONDS
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
) -> list[DeletionRegion]:
    groups = compact_groups(
        group_alignment_steps(steps),
        original_track,
        answer_track,
    )
    regions: list[DeletionRegion] = []
    pad_original = int(original_audio.sample_rate * DELETION_PAD_SECONDS)
    sound_number = 1

    for group in groups:
        role = group_role(group)
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

        if role == "deletion" and original_start is not None and original_end is not None:
            original_start = max(0, original_start - pad_original)
            original_end = min(len(original_audio.samples), original_end + pad_original)

        regions.append(
            DeletionRegion(
                name=f"sound{sound_number}",
                role=role,
                original_start=original_start,
                original_end=original_end,
                answer_start=answer_start,
                answer_end=answer_end,
                similarity=mean_similarity(group),
            )
        )
        sound_number += 1

    return regions


def write_regions(
    original: str,
    answer: str,
    original_audio: AudioData,
    answer_audio: AudioData,
    regions: list[DeletionRegion],
) -> dict[str, object]:
    output_regions: list[dict[str, object]] = []
    deletions: list[dict[str, str]] = []

    for region in regions:
        if region.role == "match":
            clip = OUTPUT_DIR / f"{region.name}.wav"
            clip_region(
                original_audio,
                region.original_start or 0,
                region.original_end or 0,
                clip,
            )
            output_regions.append(
                {
                    "id": region.name,
                    "role": "match",
                    "clip": str(clip),
                    "similarity": round(region.similarity, 3),
                    "original": {
                        "startMs": milliseconds(region.original_start or 0, original_audio.sample_rate),
                        "endMs": milliseconds(region.original_end or 0, original_audio.sample_rate),
                    },
                    "answer": {
                        "startMs": milliseconds(region.answer_start or 0, answer_audio.sample_rate),
                        "endMs": milliseconds(region.answer_end or 0, answer_audio.sample_rate),
                    },
                }
            )
            continue

        clip = OUTPUT_DIR / f"{clean_file_part(original)}_{region.name}.wav"
        clip_region(
            original_audio,
            region.original_start or 0,
            region.original_end or 0,
            clip,
        )
        deletion = {
            "id": region.name,
            "clip": str(clip),
        }
        deletions.append(deletion)
        output_regions.append(
            {
                "id": region.name,
                "role": "deletion",
                "clip": str(clip),
                "original": {
                    "startMs": milliseconds(region.original_start or 0, original_audio.sample_rate),
                    "endMs": milliseconds(region.original_end or 0, original_audio.sample_rate),
                },
                "answer": None,
            }
        )

    activity: dict[str, object] = {
        "source": "audio-only-deletion-prototype",
        "type": "deletion",
        "word": original,
        "answer": answer,
        "originalAudio": str(original_audio.path),
        "answerAudio": str(answer_audio.path),
        "regions": output_regions,
    }

    if deletions:
        activity["deletions"] = deletions

    return activity


def analyze_words(original: str, answer: str) -> dict[str, object]:
    clear_output_dir()
    original_path, answer_path = synthesize_inputs(original, answer)
    original_audio = load_audio(original_path)
    answer_audio = load_audio(answer_path)
    original_track = extract_features(original_audio)
    answer_track = extract_features(answer_audio)
    original_features, answer_features = standardize_pair(
        original_track.features,
        answer_track.features,
    )
    steps = subsequence_alignment(original_features, answer_features)
    regions = build_regions(
        steps,
        original_track,
        answer_track,
        original_audio,
        answer_audio,
    )
    return write_regions(original, answer, original_audio, answer_audio, regions)


def print_analysis(original: str, answer: str) -> None:
    activity = analyze_words(original, answer)
    print()
    print(f"Original word audio: {activity['originalAudio']}")
    print(f"Answer word audio:   {activity['answerAudio']}")
    print()
    print("Detected deletion regions:")

    for region in activity["regions"]:
        if region["role"] == "match":
            print(
                f"- {region['id']} match "
                f"(similarity {region['similarity']}): "
                f"{region['clip']}"
            )
            continue

        print(f"- {region['id']} deletion: {region['clip']}")

    print()
    print("Audio-only deletion activity draft:")
    print(json.dumps(activity, indent=2))
    print()


def run_prompt() -> None:
    print("Type a full original word and the answer after deletion.")
    print("Example: original `spill`, answer `pill`. Leave original blank to quit.")

    while True:
        original = input("Original word: ").strip()

        if not original:
            return

        answer = input("Answer word: ").strip()

        if not answer:
            print("Answer word is required.")
            continue

        print_analysis(original, answer)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audio-only deletion prototype that finds omitted audio."
    )
    parser.add_argument("original", nargs="?", help="Full original word, such as spill.")
    parser.add_argument("answer", nargs="?", help="Answer word after deletion, such as pill.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.original and args.answer:
        print_analysis(args.original, args.answer)
        return

    run_prompt()


if __name__ == "__main__":
    main()
