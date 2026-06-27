from __future__ import annotations

import hashlib
import json
import re
from html import escape
from pathlib import Path
from typing import Any

from app.core.config import API_DIR, settings
from app.services.breakdown import BREAKDOWN_VERSION
from app.services.breakdown import deletion, substitution
from app.services.breakdown.audio import clean_file_part, clip_samples, load_audio
from app.services.breakdown.models import ActivityRegion, AudioData
from app.services.audio_clips import synthesize_wav_with_boundaries
from app.services.text_to_speech import DialogueError, load_dialogue, wrap_ssml


WORD_CACHE_DIR = API_DIR / ".cache" / "activity-word-audio"
CLIP_CACHE_DIR = API_DIR / ".cache" / "activity-token-clips"
ACTIVITY_CACHE_DIR = API_DIR / ".cache" / "activity-breakdowns"


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


def clip_audio(audio: AudioData, start: int | None, end: int | None) -> bytes:
    return clip_samples(audio, start, end)


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


def analyze_substitution_audio(
    original_audio: AudioData,
    answer_audio: AudioData,
) -> list[ActivityRegion]:
    return substitution.analyze_audio(original_audio, answer_audio)


def analyze_deletion_audio(
    original_audio: AudioData,
    answer_audio: AudioData,
) -> list[ActivityRegion]:
    return deletion.analyze_audio(original_audio, answer_audio)


def analyze_activity_audio(
    activity_type: str,
    original_audio: AudioData,
    answer_audio: AudioData,
) -> list[ActivityRegion]:
    if activity_type == "deletion":
        return analyze_deletion_audio(original_audio, answer_audio)

    if activity_type == "substitution":
        return analyze_substitution_audio(original_audio, answer_audio)

    raise DialogueError(f"Unsupported activity type: {activity_type}")


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
    regions: list[ActivityRegion],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    tokens: list[dict[str, Any]] = []
    deletions: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []

    for region in regions:
        sound_label = region.name

        if activity_type == "deletion" and region.role == "deletion":
            sound_label = region.delete_sound or region.name

        if activity_type == "substitution" and region.role == "discrepancy":
            sound_label = region.from_sound or region.name

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
            "sound": sound_label,
            "role": region.role,
            "action": "none",
            "clipUrl": original_clip_url,
            "similarity": round(region.similarity, 3),
            "originalSoundLabels": list(region.original_sound_labels),
            "answerSoundLabels": list(region.answer_sound_labels),
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
            deletion = {
                "id": region.name,
                "sound": sound_label,
                "soundLabels": list(region.original_sound_labels),
                "clipUrl": original_clip_url,
            }
            deletions.append(deletion)

        if activity_type == "substitution" and region.role == "discrepancy":
            replacement_sound = region.to_sound or f"{region.name} answer"
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
            token["replacementSound"] = replacement_sound
            token["replacementClipUrl"] = answer_clip_url
            change = {
                "id": region.name,
                "fromSound": sound_label,
                "toSound": replacement_sound,
                "fromSoundLabels": list(region.original_sound_labels),
                "toSoundLabels": list(region.answer_sound_labels),
                "fromClipUrl": original_clip_url,
                "toClipUrl": answer_clip_url,
            }
            changes.append(change)

        tokens.append(token)

    return tokens, deletions, changes


def fallback_regions(original_audio: AudioData, answer_audio: AudioData) -> list[ActivityRegion]:
    return [
        ActivityRegion(
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
        "source": f"azure-audio-{activity_type}-breakdown",
        "type": activity_type,
        "word": word,
        "answer": answer,
        "tokens": tokens,
    }

    if deletions:
        prepared["deletions"] = deletions
        prepared["deleteTokenIds"] = [deletion["id"] for deletion in deletions]
        prepared["deleteTokenId"] = deletions[0]["id"]
        prepared["deleteSounds"] = [deletion["sound"] for deletion in deletions]
        prepared["deleteSoundLabels"] = [
            label
            for deletion in deletions
            for label in deletion["soundLabels"]
        ]
        prepared["deleteSound"] = raw_activity.get("deleteSound") or deletions[0]["sound"]

    if changes:
        prepared["changes"] = changes
        prepared["changeTokenIds"] = [change["id"] for change in changes]
        prepared["replaceTokenId"] = changes[0]["id"]
        prepared["fromSoundLabels"] = changes[0]["fromSoundLabels"]
        prepared["toSoundLabels"] = changes[0]["toSoundLabels"]
        prepared["fromSound"] = raw_activity.get("fromSound") or changes[0]["fromSound"]
        prepared["toSound"] = raw_activity.get("toSound") or changes[0]["toSound"]
        prepared["replaceFrom"] = prepared["fromSound"]
        prepared["replaceTo"] = prepared["toSound"]

    cache_path.write_text(json.dumps(prepared, indent=2), encoding="utf-8")
    return prepared
