from __future__ import annotations

import math

from app.services.breakdown.config import DEFAULT_FEATURE_CONFIG, FeatureConfig
from app.services.breakdown.models import AudioData, FeatureTrack, SoundUnit


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


def frame_features(
    frame: list[float],
    sample_rate: int,
    config: FeatureConfig = DEFAULT_FEATURE_CONFIG,
) -> list[float]:
    frame_rms = rms(frame)
    diff = derivative_rms(frame)
    band_powers = [
        goertzel_power(frame, sample_rate, frequency)
        for frequency in config.band_frequencies
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


def extract_features(
    audio: AudioData,
    config: FeatureConfig = DEFAULT_FEATURE_CONFIG,
) -> FeatureTrack:
    frame_length = max(1, int(audio.sample_rate * config.frame_seconds))
    hop_length = max(1, int(audio.sample_rate * config.hop_seconds))
    features: list[list[float]] = []
    frame_starts: list[int] = []
    start = audio.trim_start
    last_start = max(start, audio.trim_end - frame_length)

    while start <= last_start:
        frame = audio.samples[start:start + frame_length]
        features.append(frame_features(frame, audio.sample_rate, config))
        frame_starts.append(start)
        start += hop_length

    if not features and audio.samples:
        features.append(
            frame_features(audio.samples[audio.trim_start:audio.trim_end], audio.sample_rate, config)
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


def unit_feature_slice(features: list[list[float]], unit: SoundUnit) -> list[list[float]]:
    if not features:
        return []

    start = max(0, min(unit.start_frame, len(features) - 1))
    end = max(start, min(unit.end_frame, len(features) - 1))
    return features[start:end + 1]


def dtw_distance(left: list[list[float]], right: list[list[float]]) -> float:
    if not left or not right:
        return 0.0

    rows = len(left)
    columns = len(right)
    costs = [[math.inf for _ in range(columns + 1)] for _ in range(rows + 1)]
    costs[0][0] = 0.0

    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            distance = feature_distance(left[row - 1], right[column - 1])
            costs[row][column] = distance + min(
                costs[row - 1][column - 1],
                costs[row - 1][column],
                costs[row][column - 1],
            )

    return costs[rows][columns] / max(rows, columns)
