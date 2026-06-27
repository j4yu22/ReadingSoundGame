from __future__ import annotations

from dataclasses import dataclass

from app.services.breakdown.config import (
    DEFAULT_FEATURE_CONFIG,
    DEFAULT_MATCHING_CONFIG,
    DEFAULT_SEGMENTATION_CONFIG,
    DEFAULT_TOKEN_MERGE_CONFIG,
    FeatureConfig,
    MatchingConfig,
    SegmentationConfig,
    TokenMergeConfig,
)
from app.services.breakdown.features import extract_features, standardize_pair
from app.services.breakdown.matching import align_sound_units
from app.services.breakdown.models import ActivityRegion, AudioData, FeatureTrack, SoundMatch, SoundUnit
from app.services.breakdown.segmentation import segment_word
from app.services.breakdown.tokens import build_activity_regions


@dataclass(frozen=True)
class WordBreakdown:
    audio: AudioData
    track: FeatureTrack
    sounds: list[SoundUnit]


@dataclass(frozen=True)
class PairBreakdown:
    original: WordBreakdown
    answer: WordBreakdown
    matches: list[SoundMatch]
    regions: list[ActivityRegion]


def break_word_into_sounds(
    audio: AudioData,
    side: str,
    feature_config: FeatureConfig = DEFAULT_FEATURE_CONFIG,
    segmentation_config: SegmentationConfig = DEFAULT_SEGMENTATION_CONFIG,
) -> WordBreakdown:
    track = extract_features(audio, feature_config)
    return WordBreakdown(
        audio=audio,
        track=track,
        sounds=segment_word(audio, track, side, segmentation_config),
    )


def analyze_pair(
    original_audio: AudioData,
    answer_audio: AudioData,
    activity_type: str,
    feature_config: FeatureConfig = DEFAULT_FEATURE_CONFIG,
    segmentation_config: SegmentationConfig = DEFAULT_SEGMENTATION_CONFIG,
    matching_config: MatchingConfig = DEFAULT_MATCHING_CONFIG,
    token_config: TokenMergeConfig = DEFAULT_TOKEN_MERGE_CONFIG,
) -> PairBreakdown:
    original = break_word_into_sounds(
        original_audio,
        "original",
        feature_config,
        segmentation_config,
    )
    answer = break_word_into_sounds(
        answer_audio,
        "answer",
        feature_config,
        segmentation_config,
    )
    original_features, answer_features = standardize_pair(
        original.track.features,
        answer.track.features,
    )
    matches = align_sound_units(
        original.sounds,
        answer.sounds,
        original_features,
        answer_features,
        matching_config,
    )
    regions = build_activity_regions(activity_type, matches, token_config)

    return PairBreakdown(
        original=original,
        answer=answer,
        matches=matches,
        regions=regions,
    )
