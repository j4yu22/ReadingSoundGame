from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import wave
from array import array
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from app.core.config import API_DIR, settings
from app.services.audio_clips import synthesize_wav_with_boundaries
from app.services.text_to_speech import DialogueError, load_dialogue, wrap_ssml


BREAKDOWN_VERSION = "v4-azure-audio-breakdown"
WORD_CACHE_DIR = API_DIR / ".cache" / "activity-word-audio"
CLIP_CACHE_DIR = API_DIR / ".cache" / "activity-token-clips"
ACTIVITY_CACHE_DIR = API_DIR / ".cache" / "activity-breakdowns"

FRAME_SECONDS = 0.001
HOP_SECONDS = 0.001
SILENCE_THRESHOLD_RATIO = 0.05
SILENCE_MARGIN_SECONDS = 0.001
MIN_REGION_SECONDS = 0.005

CHANGE_END_PAD_SECONDS = 0.07
DISTANCE_SMOOTH_RADIUS = 12
DISCREPANCY_PEAK_PERCENTILE = 98
DISCREPANCY_EDGE_PERCENTILE = 90
MAX_MATCH_GAP_TO_MERGE_DISCREPANCIES_SECONDS = 0.08

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
class AudioData:
    label: str
    wav_bytes: bytes
    sample_rate: int
    samples: list[float]
    trim_start: int
    trim_end: int


@dataclass(frozen=True)
class FeatureTrack:
    features: list[list[float]]
    frame_starts: list[int]
    frame_length: int
    hop_length: int


@dataclass(frozen=True)
class Region:
    name: str
    role: str
    original_start: int | None
    original_end: int | None
    answer_start: int | None
    answer_end: int | None
    similarity: float


@dataclass(frozen=True)
class PathRun:
    is_match: bool
    start_index: int
    end_index: int


@dataclass(frozen=True)
class AlignStep:
    kind: str
    original_frame: int | None
    answer_frame: int | None
    distance: float


def clean_file_part(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    return clean or "word"


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cache_context() -> str:
    dialogue = load_dialogue()
    voice = dialogue.get("voice", {})
    return json.dumps(
        {
            "version": BREAKDOWN_VERSION,
            "voice": voice,
            "azureVoice": settings.azure_speech_voice,
        },
        sort_keys=True,
    )


def milliseconds(sample: int, sample_rate: int) -> int:
    return round((sample / sample_rate) * 1000)


def build_word_ssml(word: str) -> str:
    dialogue = load_dialogue()
    lines = dialogue.get("lines", {})
    line = lines.get("token_sound") if isinstance(lines, dict) else None

    if not isinstance(line, dict):
        line = {}

    return wrap_ssml(f"{escape(word.strip())}.", dialogue, line)


def synthesize_word_wav(word: str) -> bytes:
    WORD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hash_text(f"{cache_context()}\nword\n{word.strip().lower()}")
    path = WORD_CACHE_DIR / f"{cache_key}.wav"

    if path.is_file():
        return path.read_bytes()

    wav_audio, _ = synthesize_wav_with_boundaries(build_word_ssml(word))
    path.write_bytes(wav_audio)
    return wav_audio


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


def trim_bounds(samples: list[float], sample_rate: int) -> tuple[int, int]:
    if not samples:
        return 0, 0

    peak = max(abs(sample) for sample in samples)

    if peak <= 0:
        return 0, len(samples)

    threshold = peak * SILENCE_THRESHOLD_RATIO
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

    margin = int(sample_rate * SILENCE_MARGIN_SECONDS)
    return max(0, start - margin), min(len(samples), end + margin)


def load_audio(label: str, wav_audio: bytes) -> AudioData:
    sample_rate, samples = read_wav_mono(wav_audio)
    trim_start, trim_end = trim_bounds(samples, sample_rate)
    return AudioData(
        label=label,
        wav_bytes=wav_audio,
        sample_rate=sample_rate,
        samples=samples,
        trim_start=trim_start,
        trim_end=trim_end,
    )


def rms(frame: list[float]) -> float:
    if not frame:
        return 0.0

    return math.sqrt(sum(sample * sample for sample in frame) / len(frame))


def zero_crossing_rate(frame: list[float]) -> float:
    if len(frame) < 2:
        return 0.0

    crossings = 0

    for previous, current in zip(frame, frame[1:]):
        if (previous < 0 <= current) or (previous >= 0 > current):
            crossings += 1

    return crossings / (len(frame) - 1)


def derivative_rms(frame: list[float]) -> float:
    if len(frame) < 2:
        return 0.0

    diffs = [current - previous for previous, current in zip(frame, frame[1:])]
    return rms(diffs)


def goertzel_power(frame: list[float], sample_rate: int, frequency: float) -> float:
    if not frame or frequency >= sample_rate / 2:
        return 0.0

    length = len(frame)
    bin_index = int(0.5 + (length * frequency) / sample_rate)
    omega = (2.0 * math.pi * bin_index) / length
    coefficient = 2.0 * math.cos(omega)
    q1 = 0.0
    q2 = 0.0

    for sample in frame:
        q0 = coefficient * q1 - q2 + sample
        q2 = q1
        q1 = q0

    return q1 * q1 + q2 * q2 - coefficient * q1 * q2


def frame_features(frame: list[float], sample_rate: int) -> list[float]:
    frame_rms = rms(frame)
    diff = derivative_rms(frame)
    band_frequencies = [250, 500, 1000, 2000, 3200, 4800]
    band_powers = [
        goertzel_power(frame, sample_rate, frequency)
        for frequency in band_frequencies
    ]
    total_band_power = sum(band_powers) + 1e-9

    return [
        math.log(frame_rms + 1e-6),
        zero_crossing_rate(frame),
        diff / (frame_rms + 1e-6),
        *[
            math.log((power / total_band_power) + 1e-6)
            for power in band_powers
        ],
    ]


def extract_features(audio: AudioData) -> FeatureTrack:
    frame_length = max(1, int(audio.sample_rate * FRAME_SECONDS))
    hop_length = max(1, int(audio.sample_rate * HOP_SECONDS))
    features: list[list[float]] = []
    frame_starts: list[int] = []
    start = audio.trim_start
    last_start = max(start, audio.trim_end - frame_length)

    while start <= last_start:
        frame = audio.samples[start:start + frame_length]
        features.append(frame_features(frame, audio.sample_rate))
        frame_starts.append(start)
        start += hop_length

    if not features and audio.samples:
        features.append(
            frame_features(audio.samples[audio.trim_start:audio.trim_end], audio.sample_rate)
        )
        frame_starts.append(audio.trim_start)

    return FeatureTrack(
        features=features,
        frame_starts=frame_starts,
        frame_length=frame_length,
        hop_length=hop_length,
    )


def standardize_pair(
    left: list[list[float]],
    right: list[list[float]],
) -> tuple[list[list[float]], list[list[float]]]:
    combined = left + right

    if not combined:
        return left, right

    columns = len(combined[0])
    means = [
        sum(row[column] for row in combined) / len(combined)
        for column in range(columns)
    ]
    deviations = []

    for column in range(columns):
        variance = sum(
            (row[column] - means[column]) ** 2
            for row in combined
        ) / len(combined)
        deviations.append(math.sqrt(variance) or 1.0)

    def normalize(rows: list[list[float]]) -> list[list[float]]:
        return [
            [
                (row[column] - means[column]) / deviations[column]
                for column in range(columns)
            ]
            for row in rows
        ]

    return normalize(left), normalize(right)


def feature_distance(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0

    return math.sqrt(
        sum((left_value - right_value) ** 2 for left_value, right_value in zip(left, right))
        / len(left)
    )


def smooth(values: list[float], radius: int = 3) -> list[float]:
    smoothed = []

    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        smoothed.append(sum(values[start:end]) / (end - start))

    return smoothed


def percentile(values: list[float], percentage: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((percentage / 100) * (len(ordered) - 1))),
    )
    return ordered[index]


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


def clip_audio(audio: AudioData, start: int | None, end: int | None) -> bytes:
    safe_start = max(0, min(start or 0, len(audio.samples)))
    safe_end = max(safe_start, min(end or 0, len(audio.samples)))
    return write_wav_mono(audio.sample_rate, audio.samples[safe_start:safe_end])


def save_clip(
    activity_key: str,
    region_name: str,
    clip_role: str,
    wav_audio: bytes,
) -> str:
    CLIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    clip_key = hash_text(f"{activity_key}\n{region_name}\n{clip_role}\n{wav_audio.hex()}")
    path = CLIP_CACHE_DIR / f"{clip_key}.wav"

    if not path.is_file():
        path.write_bytes(wav_audio)

    return f"/api/activities/clips/{path.name}"


def clip_path_for_id(clip_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}\.wav", clip_id):
        raise DialogueError("Invalid clip id.")

    path = CLIP_CACHE_DIR / clip_id

    if not path.is_file():
        raise DialogueError("Clip not found.")

    return path


def dtw_path(left: list[list[float]], right: list[list[float]]) -> list[tuple[int, int, float]]:
    if not left or not right:
        return []

    rows = len(left)
    columns = len(right)
    costs = [[math.inf for _ in range(columns + 1)] for _ in range(rows + 1)]
    back: list[list[tuple[int, int] | None]] = [
        [None for _ in range(columns + 1)]
        for _ in range(rows + 1)
    ]
    costs[0][0] = 0.0

    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            distance = feature_distance(left[row - 1], right[column - 1])
            options = [
                (costs[row - 1][column - 1], row - 1, column - 1),
                (costs[row - 1][column], row - 1, column),
                (costs[row][column - 1], row, column - 1),
            ]
            previous_cost, previous_row, previous_column = min(
                options,
                key=lambda item: item[0],
            )
            costs[row][column] = distance + previous_cost
            back[row][column] = (previous_row, previous_column)

    path: list[tuple[int, int, float]] = []
    row = rows
    column = columns

    while row > 0 and column > 0:
        path.append((row - 1, column - 1, feature_distance(left[row - 1], right[column - 1])))
        previous = back[row][column]

        if previous is None:
            break

        row, column = previous

    path.reverse()
    return path


def merge_adjacent_runs(runs: list[PathRun]) -> list[PathRun]:
    merged: list[PathRun] = []

    for run in runs:
        if merged and merged[-1].is_match == run.is_match:
            previous = merged[-1]
            merged[-1] = PathRun(
                is_match=previous.is_match,
                start_index=previous.start_index,
                end_index=run.end_index,
            )
            continue

        merged.append(run)

    return merged


def merge_short_runs(runs: list[PathRun]) -> list[PathRun]:
    if len(runs) <= 1:
        return runs

    minimum_steps = max(1, round(MIN_REGION_SECONDS / max(HOP_SECONDS, 0.001)))
    merged = runs[:]
    index = 0

    while index < len(merged):
        run = merged[index]
        run_length = run.end_index - run.start_index + 1

        if run_length >= minimum_steps or len(merged) <= 1:
            index += 1
            continue

        if index == 0:
            next_run = merged[index + 1]
            merged[index + 1] = PathRun(
                is_match=next_run.is_match,
                start_index=run.start_index,
                end_index=next_run.end_index,
            )
            del merged[index]
            continue

        if index == len(merged) - 1:
            previous = merged[index - 1]
            merged[index - 1] = PathRun(
                is_match=previous.is_match,
                start_index=previous.start_index,
                end_index=run.end_index,
            )
            del merged[index]
            index = max(0, index - 1)
            continue

        previous = merged[index - 1]
        next_run = merged[index + 1]
        previous_length = previous.end_index - previous.start_index + 1
        next_length = next_run.end_index - next_run.start_index + 1

        if previous_length >= next_length:
            merged[index - 1] = PathRun(
                is_match=previous.is_match,
                start_index=previous.start_index,
                end_index=run.end_index,
            )
            del merged[index]
            index = max(0, index - 1)
        else:
            merged[index + 1] = PathRun(
                is_match=next_run.is_match,
                start_index=run.start_index,
                end_index=next_run.end_index,
            )
            del merged[index]

    return merge_adjacent_runs(merged)


def merge_nearby_discrepancies(runs: list[PathRun]) -> list[PathRun]:
    if len(runs) < 3:
        return runs

    maximum_gap_steps = max(
        1,
        round(MAX_MATCH_GAP_TO_MERGE_DISCREPANCIES_SECONDS / max(HOP_SECONDS, 0.001)),
    )
    merged = runs[:]
    index = 1

    while index < len(merged) - 1:
        previous = merged[index - 1]
        current = merged[index]
        next_run = merged[index + 1]
        current_length = current.end_index - current.start_index + 1

        if (
            current.is_match
            and not previous.is_match
            and not next_run.is_match
            and current_length <= maximum_gap_steps
        ):
            merged[index - 1] = PathRun(
                is_match=False,
                start_index=previous.start_index,
                end_index=next_run.end_index,
            )
            del merged[index:index + 2]
            index = max(1, index - 1)
            continue

        index += 1

    return merge_adjacent_runs(merged)


def build_path_runs(path: list[tuple[int, int, float]]) -> list[PathRun]:
    if not path:
        return []

    distances = smooth(
        [distance for _, _, distance in path],
        radius=DISTANCE_SMOOTH_RADIUS,
    )
    peak_threshold = percentile(distances, DISCREPANCY_PEAK_PERCENTILE)
    edge_threshold = percentile(distances, DISCREPANCY_EDGE_PERCENTILE)
    changed = [False for _ in distances]
    index = 0

    while index < len(distances):
        if distances[index] < peak_threshold:
            index += 1
            continue

        start = index
        end = index

        while start > 0 and distances[start - 1] >= edge_threshold:
            start -= 1

        while end + 1 < len(distances) and distances[end + 1] >= edge_threshold:
            end += 1

        for changed_index in range(start, end + 1):
            changed[changed_index] = True

        index = end + 1

    runs: list[PathRun] = []
    start = 0
    current_is_match = not changed[0]

    for index, is_changed in enumerate(changed[1:], start=1):
        is_match = not is_changed

        if is_match == current_is_match:
            continue

        runs.append(
            PathRun(
                is_match=current_is_match,
                start_index=start,
                end_index=index - 1,
            )
        )
        start = index
        current_is_match = is_match

    runs.append(
        PathRun(
            is_match=current_is_match,
            start_index=start,
            end_index=len(distances) - 1,
        )
    )
    return merge_nearby_discrepancies(merge_short_runs(runs))


def path_region_samples(
    path: list[tuple[int, int, float]],
    start_index: int,
    end_index: int,
    original_track: FeatureTrack,
    answer_track: FeatureTrack,
) -> tuple[int | None, int | None, int | None, int | None]:
    if start_index > end_index or not path:
        return None, None, None, None

    selected = path[start_index:end_index + 1]
    original_frames = [item[0] for item in selected]
    answer_frames = [item[1] for item in selected]
    original_start, original_end = frame_span_to_samples(
        original_track,
        min(original_frames),
        max(original_frames),
    )
    answer_start, answer_end = frame_span_to_samples(
        answer_track,
        min(answer_frames),
        max(answer_frames),
    )
    return original_start, original_end, answer_start, answer_end


def mean_path_similarity(path: list[tuple[int, int, float]], start: int, end: int) -> float:
    if start > end or not path:
        return 0.0

    distances = [distance for _, _, distance in path[start:end + 1]]
    mean_distance = sum(distances) / len(distances)
    return 1.0 / (1.0 + mean_distance)


def build_substitution_regions(
    path: list[tuple[int, int, float]],
    original_track: FeatureTrack,
    answer_track: FeatureTrack,
    original_audio: AudioData,
    answer_audio: AudioData,
) -> list[Region]:
    if not path:
        return []

    runs = build_path_runs(path)
    regions: list[Region] = []
    sound_number = 1

    for index, run in enumerate(runs):
        original_start, original_end, answer_start, answer_end = path_region_samples(
            path,
            run.start_index,
            run.end_index,
            original_track,
            answer_track,
        )

        if original_start is None or original_end is None:
            continue

        if answer_start is None or answer_end is None:
            continue

        original_end_pad = int(original_audio.sample_rate * CHANGE_END_PAD_SECONDS)
        answer_end_pad = int(answer_audio.sample_rate * CHANGE_END_PAD_SECONDS)
        previous_run = runs[index - 1] if index > 0 else None

        if previous_run and not previous_run.is_match:
            original_start += original_end_pad
            answer_start += answer_end_pad

        if not run.is_match:
            original_end += original_end_pad
            answer_end += answer_end_pad

        min_original = int(original_audio.sample_rate * MIN_REGION_SECONDS)
        min_answer = int(answer_audio.sample_rate * MIN_REGION_SECONDS)

        if original_end - original_start < min_original and answer_end - answer_start < min_answer:
            continue

        regions.append(
            Region(
                name=f"sound{sound_number}",
                role="match" if run.is_match else "discrepancy",
                original_start=original_start,
                original_end=min(original_end, len(original_audio.samples)),
                answer_start=answer_start,
                answer_end=min(answer_end, len(answer_audio.samples)),
                similarity=mean_path_similarity(path, run.start_index, run.end_index),
            )
        )
        sound_number += 1

    return regions


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
            back["delete"][original_index][0] = ("delete", original_index - 1, 0)

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
                    (costs[state][original_index - 1][answer_index - 1], state)
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

        if groups and role == group_role(groups[-1]):
            groups[-1].append(step)
            continue

        groups.append([step])

    return groups


def compact_deletion_groups(
    groups: list[list[AlignStep]],
    max_match_gap_seconds: float = MAX_MATCH_GAP_TO_MERGE_DELETIONS_SECONDS,
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
            <= max_match_gap_seconds
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
        and group_duration_seconds(merged[0]) <= MAX_EDGE_MATCH_TO_MERGE_DELETION_SECONDS
    ):
        merged[1].extend(merged[0])
        merged = merged[1:]

    if (
        len(merged) > 1
        and group_role(merged[-1]) == "match"
        and group_role(merged[-2]) == "deletion"
        and group_duration_seconds(merged[-1]) <= MAX_EDGE_MATCH_TO_MERGE_DELETION_SECONDS
    ):
        merged[-2].extend(merged[-1])
        merged = merged[:-1]

    return merged


def frames_for_group(
    group: list[AlignStep],
) -> tuple[int | None, int | None, int | None, int | None]:
    original_frames = [
        step.original_frame for step in group if step.original_frame is not None
    ]
    answer_frames = [step.answer_frame for step in group if step.answer_frame is not None]
    return (
        min(original_frames) if original_frames else None,
        max(original_frames) if original_frames else None,
        min(answer_frames) if answer_frames else None,
        max(answer_frames) if answer_frames else None,
    )


def mean_step_similarity(group: list[AlignStep]) -> float:
    distances = [step.distance for step in group if step.kind == "match"]

    if not distances:
        return 0.0

    return 1.0 / (1.0 + (sum(distances) / len(distances)))


def build_deletion_regions(
    steps: list[AlignStep],
    original_track: FeatureTrack,
    answer_track: FeatureTrack,
    original_audio: AudioData,
    max_match_gap_seconds: float = MAX_MATCH_GAP_TO_MERGE_DELETIONS_SECONDS,
) -> list[Region]:
    groups = compact_deletion_groups(
        group_alignment_steps(steps),
        max_match_gap_seconds=max_match_gap_seconds,
    )
    regions: list[Region] = []
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

        if original_start is None or original_end is None:
            continue

        if role == "deletion":
            original_start = max(0, original_start - pad_original)
            original_end = min(len(original_audio.samples), original_end + pad_original)

        regions.append(
            Region(
                name=f"sound{sound_number}",
                role=role,
                original_start=original_start,
                original_end=original_end,
                answer_start=answer_start,
                answer_end=answer_end,
                similarity=mean_step_similarity(group),
            )
        )
        sound_number += 1

    return regions


def analyze_activity_audio(
    activity_type: str,
    original_audio: AudioData,
    answer_audio: AudioData,
) -> list[Region]:
    original_track = extract_features(original_audio)
    answer_track = extract_features(answer_audio)
    original_features, answer_features = standardize_pair(
        original_track.features,
        answer_track.features,
    )

    if activity_type == "deletion":
        steps = subsequence_alignment(original_features, answer_features)
        regions = build_deletion_regions(
            steps,
            original_track,
            answer_track,
            original_audio,
        )
        trimmed_length = max(1, original_audio.trim_end - original_audio.trim_start)
        collapsed = (
            len(regions) == 1
            and regions[0].role == "deletion"
            and (regions[0].original_end or 0) - (regions[0].original_start or 0)
            > trimmed_length * 0.8
        )

        if collapsed:
            return build_deletion_regions(
                steps,
                original_track,
                answer_track,
                original_audio,
                max_match_gap_seconds=0.03,
            )

        return regions

    path = dtw_path(original_features, answer_features)
    return build_substitution_regions(
        path,
        original_track,
        answer_track,
        original_audio,
        answer_audio,
    )


def region_timing(
    audio: AudioData,
    start: int | None,
    end: int | None,
) -> dict[str, int]:
    return {
        "startMs": milliseconds(start or 0, audio.sample_rate),
        "endMs": milliseconds(end or 0, audio.sample_rate),
    }


def build_token_metadata(
    activity_key: str,
    activity_type: str,
    original_audio: AudioData,
    answer_audio: AudioData,
    regions: list[Region],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    tokens: list[dict[str, Any]] = []
    deletions: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []

    for region in regions:
        original_clip = clip_audio(
            original_audio,
            region.original_start,
            region.original_end,
        )
        original_clip_url = save_clip(
            activity_key,
            region.name,
            "original",
            original_clip,
        )
        token: dict[str, Any] = {
            "id": region.name,
            "sound": region.name,
            "role": region.role,
            "action": "none",
            "clipUrl": original_clip_url,
            "similarity": round(region.similarity, 3),
            "original": region_timing(
                original_audio,
                region.original_start,
                region.original_end,
            ),
        }

        if region.answer_start is not None and region.answer_end is not None:
            token["answer"] = region_timing(
                answer_audio,
                region.answer_start,
                region.answer_end,
            )

        if activity_type == "deletion" and region.role == "deletion":
            token["action"] = "delete"
            deletion = {"id": region.name, "clipUrl": original_clip_url}
            deletions.append(deletion)

        if activity_type == "substitution" and region.role == "discrepancy":
            answer_clip = clip_audio(
                answer_audio,
                region.answer_start,
                region.answer_end,
            )
            answer_clip_url = save_clip(
                activity_key,
                region.name,
                "answer",
                answer_clip,
            )
            token["action"] = "substitute"
            token["replacementClipUrl"] = answer_clip_url
            change = {
                "id": region.name,
                "fromClipUrl": original_clip_url,
                "toClipUrl": answer_clip_url,
            }
            changes.append(change)

        tokens.append(token)

    return tokens, deletions, changes


def fallback_regions(original_audio: AudioData, answer_audio: AudioData) -> list[Region]:
    return [
        Region(
            name="sound1",
            role="match",
            original_start=original_audio.trim_start,
            original_end=original_audio.trim_end,
            answer_start=answer_audio.trim_start,
            answer_end=answer_audio.trim_end,
            similarity=1.0,
        )
    ]


def prepared_activity_cache_path(activity_type: str, word: str, answer: str) -> Path:
    ACTIVITY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hash_text(
        f"{cache_context()}\n{activity_type}\n{word.strip().lower()}\n{answer.strip().lower()}"
    )
    return ACTIVITY_CACHE_DIR / f"{key}.json"


def prepare_activity(raw_activity: dict[str, Any], requested_type: str) -> dict[str, Any]:
    activity_type = str(raw_activity.get("type") or requested_type).strip().lower()
    word = str(raw_activity.get("word") or "").strip()
    answer = str(raw_activity.get("answer") or "").strip()

    if activity_type not in {"deletion", "substitution"}:
        raise DialogueError(f"Unsupported activity type: {activity_type}")

    if not word or not answer:
        raise DialogueError("Activities need both `word` and `answer`.")

    cache_path = prepared_activity_cache_path(activity_type, word, answer)

    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    activity_key = cache_path.stem
    original_audio = load_audio(word, synthesize_word_wav(word))
    answer_audio = load_audio(answer, synthesize_word_wav(answer))
    regions = analyze_activity_audio(activity_type, original_audio, answer_audio)

    if not regions:
        regions = fallback_regions(original_audio, answer_audio)

    tokens, deletions, changes = build_token_metadata(
        activity_key,
        activity_type,
        original_audio,
        answer_audio,
        regions,
    )
    prepared = {
        **raw_activity,
        "id": raw_activity.get("id") or f"{clean_file_part(word)}-{clean_file_part(answer)}",
        "source": "azure-audio-breakdown",
        "type": activity_type,
        "word": word,
        "answer": answer,
        "tokens": tokens,
    }

    if deletions:
        prepared["deletions"] = deletions
        prepared["deleteTokenIds"] = [deletion["id"] for deletion in deletions]
        prepared["deleteTokenId"] = deletions[0]["id"]
        prepared["deleteSound"] = deletions[0]["id"]

    if changes:
        prepared["changes"] = changes
        prepared["changeTokenIds"] = [change["id"] for change in changes]
        prepared["replaceTokenId"] = changes[0]["id"]
        prepared["replaceFrom"] = changes[0]["id"]
        prepared["replaceTo"] = f"{changes[0]['id']}-answer"

    cache_path.write_text(json.dumps(prepared, indent=2), encoding="utf-8")
    return prepared
