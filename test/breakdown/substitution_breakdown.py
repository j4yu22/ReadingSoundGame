from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"

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


@dataclass(frozen=True)
class AudioData:
    path: Path
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
    is_match: bool
    original_start: int
    original_end: int
    answer_start: int
    answer_end: int
    similarity: float


@dataclass(frozen=True)
class PathRun:
    is_match: bool
    start_index: int
    end_index: int


def clean_file_part(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    return clean or "word"


def clear_output_dir() -> None:
    if OUTPUT_DIR.is_dir():
        shutil.rmtree(OUTPUT_DIR)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def powershell_quote(value: str) -> str:
    return value.replace("'", "''")


def synthesize_with_windows_tts(text: str, path: Path, rate: int = -2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = {rate}
$synth.Volume = 100
$synth.SetOutputToWaveFile('{powershell_quote(str(path))}')
$synth.Speak('{powershell_quote(text)}')
$synth.Dispose()
"""
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Windows TTS failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def read_wav_mono(path: Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())

    if sample_width != 2:
        raise ValueError(f"Expected 16-bit PCM WAV, got sample width {sample_width}.")

    pcm = array("h")
    pcm.frombytes(frames)

    if sys.byteorder == "big":
        pcm.byteswap()

    samples: list[float] = []

    for index in range(0, len(pcm), channels):
        samples.append(sum(pcm[index:index + channels]) / (channels * 32768.0))

    return sample_rate, samples


def write_wav_mono(path: Path, sample_rate: int, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = array(
        "h",
        [
            max(-32768, min(32767, int(sample * 32767)))
            for sample in samples
        ],
    )

    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm.tobytes())


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
    band_powers = [goertzel_power(frame, sample_rate, frequency) for frequency in band_frequencies]
    total_band_power = sum(band_powers) + 1e-9

    return [
        math.log(frame_rms + 1e-6),
        zero_crossing_rate(frame),
        diff / (frame_rms + 1e-6),
        *[math.log((power / total_band_power) + 1e-6) for power in band_powers],
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
        features.append(frame_features(audio.samples[audio.trim_start:audio.trim_end], audio.sample_rate))
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
        variance = sum((row[column] - means[column]) ** 2 for row in combined) / len(combined)
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
            previous_cost, previous_row, previous_column = min(options, key=lambda item: item[0])
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
    index = min(len(ordered) - 1, max(0, round((percentage / 100) * (len(ordered) - 1))))
    return ordered[index]


def find_change_region(path: list[tuple[int, int, float]]) -> tuple[int, int]:
    if not path:
        return 0, 0

    distances = smooth([distance for _, _, distance in path], radius=4)
    low = percentile(distances, 35)
    high = percentile(distances, 82)
    threshold = low + (high - low) * 0.38
    peak_index = max(range(len(distances)), key=lambda index: distances[index])
    start = peak_index
    end = peak_index

    while start > 0 and distances[start - 1] >= threshold:
        start -= 1

    while end + 1 < len(distances) and distances[end + 1] >= threshold:
        end += 1

    return start, end


def change_threshold(distances: list[float]) -> float:
    low = percentile(distances, 35)
    high = percentile(distances, 82)
    return low + (high - low) * 0.38


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


def frame_span_to_samples(
    track: FeatureTrack,
    frame_start: int,
    frame_end: int,
) -> tuple[int, int]:
    if not track.frame_starts:
        return 0, 0

    bounded_start = max(0, min(frame_start, len(track.frame_starts) - 1))
    bounded_end = max(bounded_start, min(frame_end, len(track.frame_starts) - 1))
    sample_start = track.frame_starts[bounded_start]
    sample_end = track.frame_starts[bounded_end] + track.frame_length
    return sample_start, sample_end


def path_region_samples(
    path: list[tuple[int, int, float]],
    start_index: int,
    end_index: int,
    original_track: FeatureTrack,
    answer_track: FeatureTrack,
) -> tuple[int, int, int, int]:
    if start_index > end_index or not path:
        return 0, 0, 0, 0

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


def mean_similarity(path: list[tuple[int, int, float]], start: int, end: int) -> float:
    if start > end or not path:
        return 0.0

    distances = [distance for _, _, distance in path[start:end + 1]]
    mean_distance = sum(distances) / len(distances)
    return 1.0 / (1.0 + mean_distance)


def build_regions(
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
        if run.start_index > run.end_index:
            continue

        original_start, original_end, answer_start, answer_end = path_region_samples(
            path,
            run.start_index,
            run.end_index,
            original_track,
            answer_track,
        )
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
                is_match=run.is_match,
                original_start=original_start,
                original_end=min(original_end, len(original_audio.samples)),
                answer_start=answer_start,
                answer_end=min(answer_end, len(answer_audio.samples)),
                similarity=mean_similarity(path, run.start_index, run.end_index),
            )
        )
        sound_number += 1

    return regions


def milliseconds(sample: int, sample_rate: int) -> int:
    return round((sample / sample_rate) * 1000)


def clip_region(audio: AudioData, start: int, end: int, path: Path) -> None:
    safe_start = max(0, min(start, len(audio.samples)))
    safe_end = max(safe_start, min(end, len(audio.samples)))
    write_wav_mono(path, audio.sample_rate, audio.samples[safe_start:safe_end])


def synthesize_inputs(original: str, answer: str) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    original_path = OUTPUT_DIR / f"{clean_file_part(original)}__original.wav"
    answer_path = OUTPUT_DIR / f"{clean_file_part(answer)}__answer.wav"
    synthesize_with_windows_tts(original, original_path)
    synthesize_with_windows_tts(answer, answer_path)
    return original_path, answer_path


def load_audio(path: Path) -> AudioData:
    sample_rate, samples = read_wav_mono(path)
    trim_start, trim_end = trim_bounds(samples, sample_rate)
    return AudioData(
        path=path,
        sample_rate=sample_rate,
        samples=samples,
        trim_start=trim_start,
        trim_end=trim_end,
    )


def classify_audio_activity(regions: list[Region]) -> str:
    if any(not region.is_match for region in regions):
        return "substitution"

    return "match"


def build_audio_activity(
    original: str,
    answer: str,
    original_audio: AudioData,
    answer_audio: AudioData,
    regions: list[Region],
) -> dict[str, object]:
    clips: list[dict[str, object]] = []
    changes: list[dict[str, str]] = []

    for region in regions:
        if region.is_match:
            shared_clip = OUTPUT_DIR / f"{region.name}.wav"
            clip_region(original_audio, region.original_start, region.original_end, shared_clip)
            clips.append(
                {
                    "id": region.name,
                    "role": "match",
                    "clip": str(shared_clip),
                    "similarity": round(region.similarity, 3),
                    "original": {
                        "startMs": milliseconds(region.original_start, original_audio.sample_rate),
                        "endMs": milliseconds(region.original_end, original_audio.sample_rate),
                    },
                    "answer": {
                        "startMs": milliseconds(region.answer_start, answer_audio.sample_rate),
                        "endMs": milliseconds(region.answer_end, answer_audio.sample_rate),
                    },
                }
            )
            continue

        original_clip = OUTPUT_DIR / f"{clean_file_part(original)}_{region.name}.wav"
        answer_clip = OUTPUT_DIR / f"{clean_file_part(answer)}_{region.name}.wav"
        clip_region(original_audio, region.original_start, region.original_end, original_clip)
        clip_region(answer_audio, region.answer_start, region.answer_end, answer_clip)
        change = {
            "id": region.name,
            "fromClip": str(original_clip),
            "toClip": str(answer_clip),
        }
        changes.append(change)
        clips.append(
            {
                "id": region.name,
                "role": "discrepancy",
                "similarity": round(region.similarity, 3),
                "original": {
                    "clip": str(original_clip),
                    "startMs": milliseconds(region.original_start, original_audio.sample_rate),
                    "endMs": milliseconds(region.original_end, original_audio.sample_rate),
                },
                "answer": {
                    "clip": str(answer_clip),
                    "startMs": milliseconds(region.answer_start, answer_audio.sample_rate),
                    "endMs": milliseconds(region.answer_end, answer_audio.sample_rate),
                },
            }
        )

    activity: dict[str, object] = {
        "source": "audio-only-prototype",
        "type": classify_audio_activity(regions),
        "word": original,
        "answer": answer,
        "originalAudio": str(original_audio.path),
        "answerAudio": str(answer_audio.path),
        "regions": clips,
    }

    if changes:
        activity["changes"] = changes

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
    path = dtw_path(original_features, answer_features)
    regions = build_regions(
        path,
        original_track,
        answer_track,
        original_audio,
        answer_audio,
    )
    return build_audio_activity(original, answer, original_audio, answer_audio, regions)


def print_analysis(original: str, answer: str) -> None:
    activity = analyze_words(original, answer)
    print()
    print(f"Original word audio: {activity['originalAudio']}")
    print(f"Answer word audio:   {activity['answerAudio']}")
    print()
    print("Detected audio regions:")

    for region in activity["regions"]:
        if region["role"] == "match":
            print(
                f"- {region['id']} match "
                f"(similarity {region['similarity']}): "
                f"{region['clip']}"
            )
            continue

        print(
            f"- {region['id']} discrepancy "
            f"(similarity {region['similarity']}): "
            f"original {region['original']['clip']}, "
            f"answer {region['answer']['clip']}"
        )

    print()
    print("Audio-only activity draft:")
    print(json.dumps(activity, indent=2))
    print()


def run_prompt() -> None:
    print("Type an original word and an answer word. Leave original blank to quit.")
    print("The words are only used to generate TTS audio; analysis is audio-only.")

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
        description="Audio-only prototype that compares TTS word recordings."
    )
    parser.add_argument("original", nargs="?", help="Original word, such as spy.")
    parser.add_argument("answer", nargs="?", help="Answer word, such as sky.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.original and args.answer:
        print_analysis(args.original, args.answer)
        return

    run_prompt()


if __name__ == "__main__":
    main()
