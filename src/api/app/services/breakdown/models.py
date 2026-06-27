from __future__ import annotations

from dataclasses import dataclass, field


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
class SoundUnit:
    label: str
    side: str
    index: int
    start_sample: int
    end_sample: int
    start_frame: int
    end_frame: int

    @property
    def duration_samples(self) -> int:
        return max(0, self.end_sample - self.start_sample)


@dataclass(frozen=True)
class SoundMatch:
    role: str
    original: SoundUnit | None
    answer: SoundUnit | None
    similarity: float


@dataclass(frozen=True)
class ActivityRegion:
    name: str
    role: str
    original_start: int | None
    original_end: int | None
    answer_start: int | None
    answer_end: int | None
    similarity: float
    original_sound_labels: tuple[str, ...] = field(default_factory=tuple)
    answer_sound_labels: tuple[str, ...] = field(default_factory=tuple)

    @property
    def delete_sound(self) -> str:
        return describe_sound_labels(self.original_sound_labels)

    @property
    def from_sound(self) -> str:
        return describe_sound_labels(self.original_sound_labels)

    @property
    def to_sound(self) -> str:
        return describe_sound_labels(self.answer_sound_labels)


def describe_sound_labels(labels: tuple[str, ...] | list[str]) -> str:
    numbers: list[str] = []

    for label in labels:
        number = str(label).split(".", 1)[0].strip()

        if number and number not in numbers:
            numbers.append(number)

    if not numbers:
        return ""

    if len(numbers) == 1:
        return f"sound {numbers[0]}"

    if len(numbers) == 2:
        return f"sounds {numbers[0]} and {numbers[1]}"

    return f"sounds {', '.join(numbers[:-1])}, and {numbers[-1]}"
