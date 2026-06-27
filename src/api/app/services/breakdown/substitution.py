from __future__ import annotations

from app.services.breakdown.config import SUBSTITUTION_MATCHING_CONFIG
from app.services.breakdown.models import ActivityRegion, AudioData
from app.services.breakdown.pipeline import analyze_pair


def analyze_audio(original_audio: AudioData, answer_audio: AudioData) -> list[ActivityRegion]:
    return analyze_pair(
        original_audio,
        answer_audio,
        "substitution",
        matching_config=SUBSTITUTION_MATCHING_CONFIG,
    ).regions


def identify_substitution_sounds(regions: list[ActivityRegion]) -> tuple[str, str]:
    for region in regions:
        if region.role == "discrepancy":
            return region.from_sound, region.to_sound

    return "", ""
