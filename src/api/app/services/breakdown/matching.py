from __future__ import annotations

from app.services.breakdown.config import DEFAULT_MATCHING_CONFIG, MatchingConfig
from app.services.breakdown.features import dtw_distance, unit_feature_slice
from app.services.breakdown.models import SoundMatch, SoundUnit


def sound_unit_similarity(
    original: SoundUnit,
    answer: SoundUnit,
    original_features: list[list[float]],
    answer_features: list[list[float]],
) -> float:
    distance = dtw_distance(
        unit_feature_slice(original_features, original),
        unit_feature_slice(answer_features, answer),
    )
    return 1.0 / (1.0 + distance)


def is_sound_match(
    similarity: float,
    config: MatchingConfig = DEFAULT_MATCHING_CONFIG,
    *,
    is_direct_comparison: bool = False,
) -> bool:
    threshold = config.minimum_match_similarity

    if is_direct_comparison:
        threshold -= config.direct_match_similarity_bonus

    return similarity >= threshold


def find_forward_match(
    original: SoundUnit,
    answer_units: list[SoundUnit],
    answer_cursor: int,
    original_features: list[list[float]],
    answer_features: list[list[float]],
    config: MatchingConfig = DEFAULT_MATCHING_CONFIG,
) -> tuple[int, float] | None:
    maximum_index = len(answer_units)

    if config.maximum_answer_lookahead_sounds > 0:
        maximum_index = min(
            maximum_index,
            answer_cursor + config.maximum_answer_lookahead_sounds,
        )

    for answer_index in range(answer_cursor, maximum_index):
        similarity = sound_unit_similarity(
            original,
            answer_units[answer_index],
            original_features,
            answer_features,
        )

        if is_sound_match(
            similarity,
            config,
            is_direct_comparison=answer_index == answer_cursor,
        ):
            return answer_index, similarity

    return None


def align_sound_units(
    original_units: list[SoundUnit],
    answer_units: list[SoundUnit],
    original_features: list[list[float]],
    answer_features: list[list[float]],
    config: MatchingConfig = DEFAULT_MATCHING_CONFIG,
) -> list[SoundMatch]:
    matches: list[SoundMatch] = []
    answer_cursor = 0

    for original_unit in original_units:
        if answer_cursor >= len(answer_units):
            matches.append(
                SoundMatch(
                    role="original_unique",
                    original=original_unit,
                    answer=None,
                    similarity=config.unmatched_similarity,
                )
            )
            continue

        forward_match = find_forward_match(
            original_unit,
            answer_units,
            answer_cursor,
            original_features,
            answer_features,
            config,
        )

        if forward_match is None:
            matches.append(
                SoundMatch(
                    role="original_unique",
                    original=original_unit,
                    answer=None,
                    similarity=config.unmatched_similarity,
                )
            )
            continue

        matched_answer_index, similarity = forward_match

        for skipped_answer in answer_units[answer_cursor:matched_answer_index]:
            matches.append(
                SoundMatch(
                    role="answer_unique",
                    original=None,
                    answer=skipped_answer,
                    similarity=config.unmatched_similarity,
                )
            )

        matches.append(
            SoundMatch(
                role="match",
                original=original_unit,
                answer=answer_units[matched_answer_index],
                similarity=similarity,
            )
        )
        answer_cursor = matched_answer_index + 1

    for leftover_answer in answer_units[answer_cursor:]:
        matches.append(
            SoundMatch(
                role="answer_unique",
                original=None,
                answer=leftover_answer,
                similarity=config.unmatched_similarity,
            )
        )

    return matches
