from __future__ import annotations

from app.services.breakdown.config import (
    DEFAULT_TOKEN_MERGE_CONFIG,
    TokenMergeConfig,
)
from app.services.breakdown.models import ActivityRegion, SoundMatch, SoundUnit


def region_group_key(match: SoundMatch) -> str:
    return "match" if match.role == "match" else "unmatched"


def group_matches(
    matches: list[SoundMatch],
    config: TokenMergeConfig = DEFAULT_TOKEN_MERGE_CONFIG,
) -> list[list[SoundMatch]]:
    if not matches:
        return []

    groups: list[list[SoundMatch]] = []

    for match in matches:
        key = region_group_key(match)

        if groups and region_group_key(groups[-1][-1]) == key:
            if key == "match" and not config.merge_adjacent_matches:
                groups.append([match])
                continue

            if key == "unmatched" and not config.merge_adjacent_unmatched_sounds:
                groups.append([match])
                continue

            groups[-1].append(match)
            continue

        groups.append([match])

    return groups


def unit_span(units: list[SoundUnit]) -> tuple[int | None, int | None]:
    if not units:
        return None, None

    return min(unit.start_sample for unit in units), max(unit.end_sample for unit in units)


def mean_similarity(matches: list[SoundMatch]) -> float:
    values = [match.similarity for match in matches if match.role == "match"]

    if not values:
        return 0.0

    return sum(values) / len(values)


def role_for_group(activity_type: str, original_units: list[SoundUnit], answer_units: list[SoundUnit]) -> str:
    if original_units and answer_units:
        return "match"

    if original_units and activity_type == "deletion":
        return "deletion"

    if original_units:
        return "discrepancy"

    if answer_units and activity_type == "substitution":
        return "discrepancy"

    return "answer_extra"


def activity_role_for_group(
    activity_type: str,
    group: list[SoundMatch],
) -> str:
    if all(match.role == "match" for match in group):
        return "match"

    original_units = [match.original for match in group if match.original]
    answer_units = [match.answer for match in group if match.answer]

    if original_units and answer_units:
        return "discrepancy"

    return role_for_group(activity_type, original_units, answer_units)


def build_activity_regions(
    activity_type: str,
    matches: list[SoundMatch],
    config: TokenMergeConfig = DEFAULT_TOKEN_MERGE_CONFIG,
) -> list[ActivityRegion]:
    regions: list[ActivityRegion] = []

    for index, group in enumerate(group_matches(matches, config), start=1):
        original_units = [match.original for match in group if match.original]
        answer_units = [match.answer for match in group if match.answer]
        original_start, original_end = unit_span(original_units)
        answer_start, answer_end = unit_span(answer_units)

        regions.append(
            ActivityRegion(
                name=f"sound{index}",
                role=activity_role_for_group(activity_type, group),
                original_start=original_start,
                original_end=original_end,
                answer_start=answer_start,
                answer_end=answer_end,
                similarity=mean_similarity(group),
                original_sound_labels=tuple(unit.label for unit in original_units),
                answer_sound_labels=tuple(unit.label for unit in answer_units),
            )
        )

    return regions
