from __future__ import annotations

from dataclasses import dataclass


BREAKDOWN_VERSION = "v11-deletion-layout-fallback"


@dataclass(frozen=True)
class AudioLoadConfig:
    silence_threshold_ratio: float = 0.05
    silence_margin_seconds: float = 0.05


@dataclass(frozen=True)
class FeatureConfig:
    frame_seconds: float = 0.001
    hop_seconds: float = 0.001
    band_frequencies: tuple[int, ...] = (250, 500, 1000, 2000, 3200, 4800)


@dataclass(frozen=True)
class SegmentationConfig:
    boundary_smoothing_radius_frames: int = 12
    boundary_peak_percentile: float = 88
    boundary_edge_percentile: float = 72
    minimum_boundary_score: float = 0.16
    minimum_sound_seconds: float = 0.035
    minimum_boundary_gap_seconds: float = 0.045
    maximum_sound_seconds_before_soft_split: float = 0.48
    boundary_padding_seconds: float = 0.006


@dataclass(frozen=True)
class MatchingConfig:
    minimum_match_similarity: float = 0.78
    direct_match_similarity_bonus: float = 0.0
    maximum_answer_lookahead_sounds: int = 6
    unmatched_similarity: float = 0.0


@dataclass(frozen=True)
class TokenMergeConfig:
    merge_adjacent_matches: bool = True
    merge_adjacent_unmatched_sounds: bool = True
    minimum_region_seconds: float = 0.025


@dataclass(frozen=True)
class DeletionFrameAlignmentConfig:
    original_gap_open_penalty: float = 1.2
    original_gap_extend_penalty: float = 0.18
    answer_gap_open_penalty: float = 35.0
    answer_gap_extend_penalty: float = 1.0
    minimum_deletion_seconds: float = 0.012
    minimum_match_seconds: float = 0.025
    deletion_padding_seconds: float = 0.02
    max_match_gap_to_merge_deletions_seconds: float = 0.08
    max_edge_match_to_merge_deletion_seconds: float = 0.08


DEFAULT_AUDIO_LOAD_CONFIG = AudioLoadConfig()
DEFAULT_FEATURE_CONFIG = FeatureConfig()
DEFAULT_SEGMENTATION_CONFIG = SegmentationConfig()
DEFAULT_MATCHING_CONFIG = MatchingConfig()
SUBSTITUTION_MATCHING_CONFIG = MatchingConfig(
    minimum_match_similarity=0.59,
    direct_match_similarity_bonus=0.0,
    maximum_answer_lookahead_sounds=6,
    unmatched_similarity=0.0,
)
DEFAULT_TOKEN_MERGE_CONFIG = TokenMergeConfig()
DEFAULT_DELETION_FRAME_ALIGNMENT_CONFIG = DeletionFrameAlignmentConfig()
