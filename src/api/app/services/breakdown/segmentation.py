from __future__ import annotations

from app.services.breakdown.config import (
    DEFAULT_SEGMENTATION_CONFIG,
    SegmentationConfig,
)
from app.services.breakdown.features import feature_distance
from app.services.breakdown.models import AudioData, FeatureTrack, SoundUnit


def smooth(values: list[float], radius: int) -> list[float]:
    if radius <= 0:
        return values[:]

    smoothed: list[float] = []

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


def sample_to_frame(track: FeatureTrack, sample: int) -> int:
    if not track.frame_starts:
        return 0

    for index, start in enumerate(track.frame_starts):
        if start >= sample:
            return index

    return len(track.frame_starts) - 1


def frame_to_sample(track: FeatureTrack, frame_index: int) -> int:
    if not track.frame_starts:
        return 0

    bounded = max(0, min(frame_index, len(track.frame_starts) - 1))
    return track.frame_starts[bounded]


def frame_change_scores(track: FeatureTrack) -> list[float]:
    if len(track.features) < 2:
        return []

    return [
        feature_distance(previous, current)
        for previous, current in zip(track.features, track.features[1:])
    ]


def is_local_peak(values: list[float], index: int) -> bool:
    previous_value = values[index - 1] if index > 0 else values[index]
    next_value = values[index + 1] if index + 1 < len(values) else values[index]
    return values[index] >= previous_value and values[index] >= next_value


def find_boundary_frames(
    track: FeatureTrack,
    audio: AudioData,
    config: SegmentationConfig = DEFAULT_SEGMENTATION_CONFIG,
) -> list[int]:
    scores = smooth(
        frame_change_scores(track),
        config.boundary_smoothing_radius_frames,
    )

    if not scores:
        return []

    peak_threshold = max(
        config.minimum_boundary_score,
        percentile(scores, config.boundary_peak_percentile),
    )
    edge_threshold = max(
        config.minimum_boundary_score * 0.7,
        percentile(scores, config.boundary_edge_percentile),
    )
    minimum_gap_frames = max(
        1,
        round(config.minimum_boundary_gap_seconds * audio.sample_rate / track.hop_length),
    )
    boundaries: list[int] = []
    index = 0

    while index < len(scores):
        if scores[index] < peak_threshold or not is_local_peak(scores, index):
            index += 1
            continue

        start = index
        end = index

        while start > 0 and scores[start - 1] >= edge_threshold:
            start -= 1

        while end + 1 < len(scores) and scores[end + 1] >= edge_threshold:
            end += 1

        peak_index = max(range(start, end + 1), key=lambda candidate: scores[candidate])

        if not boundaries or peak_index - boundaries[-1] >= minimum_gap_frames:
            boundaries.append(peak_index + 1)
        elif scores[peak_index] > scores[boundaries[-1] - 1]:
            boundaries[-1] = peak_index + 1

        index = end + 1

    return boundaries


def split_long_regions(
    boundary_frames: list[int],
    track: FeatureTrack,
    audio: AudioData,
    config: SegmentationConfig,
) -> list[int]:
    if config.maximum_sound_seconds_before_soft_split <= 0:
        return boundary_frames

    start_frame = sample_to_frame(track, audio.trim_start)
    end_frame = sample_to_frame(track, audio.trim_end)
    maximum_frames = max(
        1,
        round(config.maximum_sound_seconds_before_soft_split * audio.sample_rate / track.hop_length),
    )
    boundaries = sorted(set(boundary_frames))
    all_edges = [start_frame, *boundaries, end_frame]
    added: list[int] = []

    for start, end in zip(all_edges, all_edges[1:]):
        if end - start <= maximum_frames:
            continue

        cursor = start + maximum_frames

        while cursor < end:
            added.append(cursor)
            cursor += maximum_frames

    return sorted(set([*boundaries, *added]))


def merge_short_segments(
    segments: list[tuple[int, int]],
    audio: AudioData,
    config: SegmentationConfig,
) -> list[tuple[int, int]]:
    minimum_samples = int(audio.sample_rate * config.minimum_sound_seconds)
    merged: list[tuple[int, int]] = []

    for start, end in segments:
        if end - start >= minimum_samples or not merged:
            merged.append((start, end))
            continue

        previous_start, _ = merged[-1]
        merged[-1] = (previous_start, end)

    if len(merged) > 1:
        first_start, first_end = merged[0]

        if first_end - first_start < minimum_samples:
            second_start, second_end = merged[1]
            merged[1] = (min(first_start, second_start), second_end)
            merged = merged[1:]

    return merged


def segment_word(
    audio: AudioData,
    track: FeatureTrack,
    side: str,
    config: SegmentationConfig = DEFAULT_SEGMENTATION_CONFIG,
) -> list[SoundUnit]:
    if not track.frame_starts:
        return []

    padding = int(audio.sample_rate * config.boundary_padding_seconds)
    boundary_frames = split_long_regions(
        find_boundary_frames(track, audio, config),
        track,
        audio,
        config,
    )
    boundaries = [
        max(audio.trim_start, min(audio.trim_end, frame_to_sample(track, frame_index)))
        for frame_index in boundary_frames
    ]
    raw_segments: list[tuple[int, int]] = []
    cursor = audio.trim_start

    for boundary in boundaries:
        start = cursor
        end = boundary

        if end > start:
            raw_segments.append((max(audio.trim_start, start - padding), min(audio.trim_end, end + padding)))

        cursor = boundary

    if audio.trim_end > cursor:
        raw_segments.append((max(audio.trim_start, cursor - padding), audio.trim_end))

    merged_segments = merge_short_segments(raw_segments, audio, config)
    suffix = "1" if side == "original" else "2"
    units: list[SoundUnit] = []

    for index, (start, end) in enumerate(merged_segments, start=1):
        units.append(
            SoundUnit(
                label=f"{index}.{suffix}",
                side=side,
                index=index,
                start_sample=start,
                end_sample=end,
                start_frame=sample_to_frame(track, start),
                end_frame=sample_to_frame(track, end),
            )
        )

    return units
