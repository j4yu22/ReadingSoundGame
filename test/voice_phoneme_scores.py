from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import shutil
import statistics
import struct
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILES = (
    REPO_ROOT / ".env",
    REPO_ROOT / "test" / "arthur" / ".env",
    REPO_ROOT / "src" / "api" / ".env",
)
OUTPUT_DIR = REPO_ROOT / "test" / "output" / "voice-phoneme-scores"

VOICE_LOCALE = "en-US"
ASSESSMENT_LANGUAGE = os.getenv("AZURE_STT_LANGUAGE", "en-US")

# Safer default while tuning. Use --all-voices for the full male/female Azure list.
DEFAULT_VOICES = (
    "en-US-AndrewNeural",
    "en-US-GuyNeural",
    "en-US-BrianNeural",
    "en-US-ChristopherNeural",
    "en-US-DavisNeural",
    "en-US-RogerNeural",
    "en-US-SteffanNeural",
    "en-US-TonyNeural",
    "en-US-JennyNeural",
    "en-US-AriaNeural",
    "en-US-AvaNeural",
    "en-US-EmmaNeural",
)

INCLUDE_GENDERS = {"Male", "Female"}
SKIP_VOICE_NAME_PARTS: tuple[str, ...] = ()

# TTS tuning values. These apply to the sound or word sample spoken for each
# phoneme. Isolated mode uses SSML phoneme tags.
SYNTHESIS_RATE = "0%"
SYNTHESIS_PITCH = "+0%"
SYNTHESIS_VOLUME = "+0%"
SYNTHESIS_FORMAT = "Riff24Khz16BitMonoPcm"
ISOLATED_LEADING_SILENCE_MS = 120
ISOLATED_TRAILING_SILENCE_MS = 180

# Batch tuning. Azure can throttle when this is run across every voice.
REQUEST_DELAY_SECONDS = 0.35
MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2.0
REUSE_EXISTING_RESULTS = True

# Composite score shown in voice_summary.csv. Change these if you care about one
# part of Azure's pronunciation assessment more than the others.
COMPOSITE_WEIGHTS = {
    "pronunciation": 0.35,
    "accuracy": 0.25,
    "phoneme_accuracy": 0.25,
    "completeness": 0.15,
}


@dataclass(frozen=True)
class PhonemeCase:
    label: str
    group: str
    reference_text: str
    ipa: str
    fallback_text: str
    assessment_text: str
    note: str = ""


# Transcribed from the user's 44 phoneme PDF. The PDF also includes /y//u/ as
# "2 sounds"; it is intentionally not included here so the run stays at 44.
PHONEMES: tuple[PhonemeCase, ...] = (
    PhonemeCase("/b/", "consonant", "ball", "b", "b", "b"),
    PhonemeCase("/d/", "consonant", "dog", "d", "d", "d"),
    PhonemeCase("/f/", "consonant", "fan", "f", "f", "f"),
    PhonemeCase("/g/", "consonant", "grapes", "ɡ", "g", "g"),
    PhonemeCase("/h/", "consonant", "hat", "h", "h", "h"),
    PhonemeCase("/j/", "consonant", "jellyfish", "dʒ", "j", "j"),
    PhonemeCase("/k/", "consonant", "kite", "k", "k", "k"),
    PhonemeCase("/l/", "consonant", "leaf", "l", "l", "l"),
    PhonemeCase("/m/", "consonant", "monkey", "m", "m", "m"),
    PhonemeCase("/n/", "consonant", "nest", "n", "n", "n"),
    PhonemeCase("/ng/", "consonant", "ring", "ŋ", "ng", "ng"),
    PhonemeCase("/p/", "consonant", "pig", "p", "p", "p"),
    PhonemeCase("/r/", "consonant", "robot", "ɹ", "r", "r"),
    PhonemeCase("/s/", "consonant", "sun", "s", "s", "s"),
    PhonemeCase("/t/", "consonant", "tap", "t", "t", "t"),
    PhonemeCase("/v/", "consonant", "van", "v", "v", "v"),
    PhonemeCase("/w/", "consonant", "web", "w", "w", "w"),
    PhonemeCase("/y/", "consonant", "yo-yo", "j", "y", "y"),
    PhonemeCase("/z/", "consonant", "zebra", "z", "z", "z"),
    PhonemeCase("/zh/", "digraph", "treasure", "ʒ", "zh", "zh"),
    PhonemeCase("/ch/", "digraph", "cheese", "tʃ", "ch", "ch"),
    PhonemeCase("/sh/", "digraph", "shark", "ʃ", "sh", "sh"),
    PhonemeCase("/th/ unvoiced", "digraph", "thing", "θ", "th", "th"),
    PhonemeCase("/th/ voiced", "digraph", "feather", "ð", "th", "th"),
    PhonemeCase("/a/", "short vowel", "cat", "æ", "a", "a"),
    PhonemeCase("/e/", "short vowel", "egg", "ɛ", "e", "e"),
    PhonemeCase("/i/", "short vowel", "igloo", "ɪ", "i", "i"),
    PhonemeCase("/o/", "short vowel", "orange", "ɑ", "o", "o"),
    PhonemeCase("/u/", "short vowel", "mug", "ʌ", "u", "u"),
    PhonemeCase("/oo/ short", "short vowel", "book", "ʊ", "oo", "oo"),
    PhonemeCase("/long a/", "long vowel", "snail", "eɪ", "ay", "ay"),
    PhonemeCase("/long e/", "long vowel", "bee", "i", "ee", "ee"),
    PhonemeCase("/long i/", "long vowel", "spider", "aɪ", "eye", "eye"),
    PhonemeCase("/long o/", "long vowel", "boat", "oʊ", "oh", "oh"),
    PhonemeCase("/long oo/", "long vowel", "moon", "u", "oo", "oo"),
    PhonemeCase("/air/", "controlled vowel", "chair", "ɛɹ", "air", "air"),
    PhonemeCase("/ar/", "controlled vowel", "car", "ɑɹ", "ar", "ar"),
    PhonemeCase("/er/", "controlled vowel", "bird", "ɝ", "er", "er"),
    PhonemeCase("/aw/", "controlled vowel", "paw", "ɔ", "aw", "aw"),
    PhonemeCase("/ear/", "controlled vowel", "ear", "ɪɹ", "ear", "ear"),
    PhonemeCase("/ure/", "controlled vowel", "cure", "jʊɹ", "ure", "ure"),
    PhonemeCase("/oi/", "diphthong", "coin", "ɔɪ", "oy", "oy"),
    PhonemeCase("/ow/", "diphthong", "cow", "aʊ", "ow", "ow"),
    PhonemeCase("/schwa/", "unstressed vowel", "ladder", "ə", "uh", "uh"),
)


SUMMARY_FIELDS = (
    "voice",
    "gender",
    "locale",
    "cases",
    "recognized_cases",
    "canceled_cases",
    "no_match_cases",
    "composite_score",
    "pronunciation_avg",
    "accuracy_avg",
    "fluency_avg",
    "completeness_avg",
    "word_accuracy_avg",
    "phoneme_accuracy_avg",
    "syllable_accuracy_avg",
    "duration_avg_seconds",
    "peak_dbfs_avg",
    "rms_dbfs_avg",
    "non_silent_seconds_avg",
    "clipping_percent_avg",
)

DETAIL_FIELDS = (
    "voice",
    "gender",
    "locale",
    "phoneme",
    "group",
    "ipa",
    "reference_text",
    "assessment_text",
    "recognized_text",
    "result_reason",
    "error",
    "duration_seconds",
    "peak_dbfs",
    "rms_dbfs",
    "non_silent_seconds",
    "clipping_percent",
    "pronunciation",
    "accuracy",
    "fluency",
    "completeness",
    "word_accuracy",
    "phoneme_accuracy",
    "syllable_accuracy",
    "audio_path",
    "json_path",
)


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_local_env() -> None:
    for path in ENV_FILES:
        load_env_file(path)


def slug(value: str) -> str:
    value = value.replace("/", "_").replace("\\", "_")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value.strip("_") or "item"


def enum_name(value: Any) -> str:
    return getattr(value, "name", str(value).split(".")[-1])


def get_voice_name(voice: Any) -> str:
    return (
        getattr(voice, "short_name", "")
        or getattr(voice, "name", "")
        or str(voice)
    )


def get_voice_gender(voice: Any) -> str:
    return enum_name(getattr(voice, "gender", "Unknown"))


def get_voice_locale(voice: Any) -> str:
    return getattr(voice, "locale", "") or VOICE_LOCALE


def score_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.fmean(values)


def rounded(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"


def get_assessment(node: dict[str, Any]) -> dict[str, Any]:
    assessment = node.get("PronunciationAssessment")
    if isinstance(assessment, dict):
        return assessment
    return {}


def collect_accuracy(nodes: Any, child_key: str | None = None) -> list[float]:
    values: list[float] = []
    if not isinstance(nodes, list):
        return values

    for node in nodes:
        if not isinstance(node, dict):
            continue
        if child_key:
            values.extend(collect_accuracy(node.get(child_key)))
            continue
        score = score_value(get_assessment(node).get("AccuracyScore"))
        if score is not None:
            values.append(score)
    return values


def parse_assessment(details: dict[str, Any]) -> dict[str, Any]:
    nbest = details.get("NBest")
    if not isinstance(nbest, list) or not nbest or not isinstance(nbest[0], dict):
        return {
            "recognized_text": details.get("DisplayText", ""),
            "pronunciation": None,
            "accuracy": None,
            "fluency": None,
            "completeness": None,
            "word_accuracy": None,
            "phoneme_accuracy": None,
            "syllable_accuracy": None,
        }

    best = nbest[0]
    overall = get_assessment(best)
    words = best.get("Words")

    word_scores = collect_accuracy(words)
    phoneme_scores = collect_accuracy(words, "Phonemes")
    syllable_scores = collect_accuracy(words, "Syllables")

    return {
        "recognized_text": details.get("DisplayText") or best.get("Display") or "",
        "pronunciation": score_value(overall.get("PronScore")),
        "accuracy": score_value(overall.get("AccuracyScore")),
        "fluency": score_value(overall.get("FluencyScore")),
        "completeness": score_value(overall.get("CompletenessScore")),
        "word_accuracy": average(word_scores),
        "phoneme_accuracy": average(phoneme_scores),
        "syllable_accuracy": average(syllable_scores),
    }


def audio_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except (EOFError, wave.Error, OSError, ZeroDivisionError):
        return None


def audio_signal_metrics(path: Path) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {
        "peak_dbfs": None,
        "rms_dbfs": None,
        "non_silent_seconds": None,
        "clipping_percent": None,
    }

    try:
        with wave.open(str(path), "rb") as wav:
            sample_width = wav.getsampwidth()
            channels = wav.getnchannels()
            frame_rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
    except (EOFError, wave.Error, OSError, ZeroDivisionError):
        return metrics

    if sample_width != 2 or not raw or channels <= 0 or frame_rate <= 0:
        return metrics

    sample_count = len(raw) // 2
    if sample_count <= 0:
        return metrics

    samples = struct.unpack(f"<{sample_count}h", raw)
    mono_samples = samples[::channels]
    if not mono_samples:
        return metrics

    max_sample = 32767.0
    peak = max(abs(sample) for sample in mono_samples)
    rms = math.sqrt(sum(sample * sample for sample in mono_samples) / len(mono_samples))
    clipping_count = sum(1 for sample in mono_samples if abs(sample) >= 32700)
    non_silent_count = sum(1 for sample in mono_samples if abs(sample) >= 327)

    metrics["peak_dbfs"] = 20 * math.log10(peak / max_sample) if peak else -120.0
    metrics["rms_dbfs"] = 20 * math.log10(rms / max_sample) if rms else -120.0
    metrics["non_silent_seconds"] = non_silent_count / float(frame_rate)
    metrics["clipping_percent"] = (clipping_count / len(mono_samples)) * 100
    return metrics


def cancellation_error(speechsdk: Any, result: Any) -> str:
    try:
        cancellation = speechsdk.CancellationDetails(result)
        return cancellation.error_details or str(cancellation.reason)
    except Exception as exc:
        return f"{result.reason}: {exc}"


def retryable_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        part in lowered
        for part in (
            "429",
            "too many",
            "throttl",
            "timeout",
            "temporarily",
            "connection",
            "service unavailable",
        )
    )


def sleep_between_requests(delay: float) -> None:
    if delay > 0:
        time.sleep(delay)


def list_azure_voices(speechsdk: Any, key: str, region: str) -> list[dict[str, str]]:
    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=None,
    )
    result = synthesizer.get_voices_async(VOICE_LOCALE).get()

    if result.reason != speechsdk.ResultReason.VoicesListRetrieved:
        raise RuntimeError(f"Could not get Azure voices: {result.reason}")

    voices: list[dict[str, str]] = []
    for voice in result.voices:
        name = get_voice_name(voice)
        gender = get_voice_gender(voice)
        locale = get_voice_locale(voice)

        if locale != VOICE_LOCALE:
            continue
        if gender not in INCLUDE_GENDERS:
            continue
        if any(skip.lower() in name.lower() for skip in SKIP_VOICE_NAME_PARTS):
            continue

        voices.append({"name": name, "gender": gender, "locale": locale})

    return sorted(voices, key=lambda item: (item["gender"], item["name"]))


def select_voices(
    all_voices: list[dict[str, str]],
    requested_names: list[str],
    use_all: bool,
    limit: int,
) -> list[dict[str, str]]:
    by_name = {voice["name"]: voice for voice in all_voices}

    if requested_names:
        selected = []
        for name in requested_names:
            voice = by_name.get(name)
            if voice is None:
                selected.append({"name": name, "gender": "Unknown", "locale": VOICE_LOCALE})
            else:
                selected.append(voice)
    elif use_all:
        selected = all_voices
    else:
        selected = [by_name.get(name, {"name": name, "gender": "Unknown", "locale": VOICE_LOCALE}) for name in DEFAULT_VOICES]

    if limit > 0:
        selected = selected[:limit]
    return selected


def build_ssml(voice_name: str, case: PhonemeCase, mode: str) -> str:
    if mode == "isolated":
        body = (
            f'<break time="{ISOLATED_LEADING_SILENCE_MS}ms"/>'
            f'<phoneme alphabet="ipa" ph="{html.escape(case.ipa)}">'
            f"{html.escape(case.fallback_text)}"
            "</phoneme>"
            f'<break time="{ISOLATED_TRAILING_SILENCE_MS}ms"/>'
        )
    else:
        spoken_text = case.reference_text.strip()
        if spoken_text and spoken_text[-1] not in ".!?":
            spoken_text += "."
        body = html.escape(spoken_text)

    return (
        '<speak version="1.0" xml:lang="en-US" '
        'xmlns="http://www.w3.org/2001/10/synthesis">'
        f'<voice name="{html.escape(voice_name)}">'
        f'<prosody rate="{SYNTHESIS_RATE}" pitch="{SYNTHESIS_PITCH}" volume="{SYNTHESIS_VOLUME}">'
        f"{body}"
        "</prosody>"
        "</voice>"
        "</speak>"
    )


def configure_synthesis_format(speechsdk: Any, speech_config: Any) -> None:
    output_format = getattr(
        speechsdk.SpeechSynthesisOutputFormat,
        SYNTHESIS_FORMAT,
    )
    speech_config.set_speech_synthesis_output_format(output_format)


def synthesize_audio(
    speechsdk: Any,
    key: str,
    region: str,
    voice_name: str,
    case: PhonemeCase,
    mode: str,
    audio_path: Path,
    delay: float,
    force: bool,
) -> tuple[bool, str]:
    if audio_path.is_file() and REUSE_EXISTING_RESULTS and not force:
        return True, ""

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        speech_config.speech_synthesis_voice_name = voice_name
        configure_synthesis_format(speechsdk, speech_config)

        audio_config = speechsdk.audio.AudioOutputConfig(filename=str(audio_path))
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        try:
            result = synthesizer.speak_ssml_async(build_ssml(voice_name, case, mode)).get()
        except Exception as exc:
            last_error = str(exc)
            if not retryable_error(last_error) or attempt == MAX_RETRIES:
                break
            time.sleep(RETRY_BASE_SECONDS * attempt)
            continue
        sleep_between_requests(delay)

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return True, ""

        last_error = cancellation_error(speechsdk, result)
        if not retryable_error(last_error) or attempt == MAX_RETRIES:
            break

        time.sleep(RETRY_BASE_SECONDS * attempt)

    return False, last_error


def assess_audio(
    speechsdk: Any,
    speech_config: Any,
    audio_path: Path,
    reference_text: str,
    delay: float,
) -> tuple[str, dict[str, Any] | None, str]:
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            audio_config = speechsdk.audio.AudioConfig(filename=str(audio_path))
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                audio_config=audio_config,
            )

            pronunciation_config = speechsdk.PronunciationAssessmentConfig(
                reference_text=reference_text,
                grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
                granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
                enable_miscue=True,
            )

            try:
                pronunciation_config.phoneme_alphabet = "IPA"
                pronunciation_config.nbest_phoneme_count = 5
            except AttributeError:
                pass

            pronunciation_config.apply_to(recognizer)
            result = recognizer.recognize_once_async().get()
        except Exception as exc:
            last_error = str(exc)
            if not retryable_error(last_error) or attempt == MAX_RETRIES:
                return "assessment_error", None, last_error
            time.sleep(RETRY_BASE_SECONDS * attempt)
            continue
        sleep_between_requests(delay)

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            details_json = result.properties.get(
                speechsdk.PropertyId.SpeechServiceResponse_JsonResult
            )
            if not details_json:
                return "recognized", None, "No pronunciation JSON returned."
            try:
                return "recognized", json.loads(details_json), ""
            except json.JSONDecodeError as exc:
                return "recognized", None, f"Could not parse pronunciation JSON: {exc}"

        if result.reason == speechsdk.ResultReason.NoMatch:
            return "no_match", None, "No speech matched."

        if result.reason == speechsdk.ResultReason.Canceled:
            last_error = cancellation_error(speechsdk, result)
            if not retryable_error(last_error) or attempt == MAX_RETRIES:
                return "canceled", None, last_error
            time.sleep(RETRY_BASE_SECONDS * attempt)
            continue

        return str(result.reason), None, ""

    return "canceled", None, last_error


def assessment_reference(case: PhonemeCase, mode: str) -> str:
    if mode == "isolated":
        return case.assessment_text
    return case.reference_text


def display_source(case: PhonemeCase, mode: str) -> str:
    if mode == "isolated":
        return f"{case.ipa} ({case.fallback_text})"
    return case.reference_text


def detail_paths(output_dir: Path, voice_name: str, case: PhonemeCase) -> tuple[Path, Path]:
    base = f"{slug(voice_name)}__{slug(case.label)}__{slug(case.reference_text)}"
    return output_dir / "audio" / f"{base}.wav", output_dir / "json" / f"{base}.json"


def score_one_case(
    speechsdk: Any,
    key: str,
    region: str,
    speech_config: Any,
    output_dir: Path,
    voice: dict[str, str],
    case: PhonemeCase,
    mode: str,
    delay: float,
    force: bool,
) -> dict[str, Any]:
    audio_path, json_path = detail_paths(output_dir, voice["name"], case)
    expected = assessment_reference(case, mode)
    row: dict[str, Any] = {
        "voice": voice["name"],
        "gender": voice["gender"],
        "locale": voice["locale"],
        "phoneme": case.label,
        "group": case.group,
        "ipa": case.ipa,
        "reference_text": case.reference_text,
        "assessment_text": expected,
        "recognized_text": "",
        "result_reason": "",
        "error": "",
        "duration_seconds": "",
        "peak_dbfs": "",
        "rms_dbfs": "",
        "non_silent_seconds": "",
        "clipping_percent": "",
        "pronunciation": "",
        "accuracy": "",
        "fluency": "",
        "completeness": "",
        "word_accuracy": "",
        "phoneme_accuracy": "",
        "syllable_accuracy": "",
        "audio_path": str(audio_path),
        "json_path": str(json_path),
    }

    cached_details: dict[str, Any] | None = None
    cached = False
    if json_path.is_file() and REUSE_EXISTING_RESULTS and not force:
        try:
            cached_payload = json.loads(json_path.read_text(encoding="utf-8"))
            cached_details = cached_payload.get("details")
            row["result_reason"] = cached_payload.get("result_reason", "")
            row["error"] = cached_payload.get("error", "")
            cached = True
        except json.JSONDecodeError:
            cached_details = None

    if not cached:
        ok, synth_error = synthesize_audio(
            speechsdk,
            key,
            region,
            voice["name"],
            case,
            mode,
            audio_path,
            delay,
            force,
        )
        if not ok:
            row["result_reason"] = "synthesis_canceled"
            row["error"] = synth_error
            return row

        result_reason, details, assessment_error = assess_audio(
            speechsdk,
            speech_config,
            audio_path,
            expected,
            delay,
        )
        row["result_reason"] = result_reason
        row["error"] = assessment_error
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(
                {
                    "voice": voice,
                    "phoneme": case.__dict__,
                    "mode": mode,
                    "assessment_text": expected,
                    "result_reason": result_reason,
                    "error": assessment_error,
                    "details": details,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    else:
        details = cached_details

    duration = audio_duration_seconds(audio_path)
    row["duration_seconds"] = rounded(duration)
    for key_name, value in audio_signal_metrics(audio_path).items():
        row[key_name] = rounded(value)

    if isinstance(details, dict):
        parsed = parse_assessment(details)
        row["recognized_text"] = parsed["recognized_text"]
        for key_name in (
            "pronunciation",
            "accuracy",
            "fluency",
            "completeness",
            "word_accuracy",
            "phoneme_accuracy",
            "syllable_accuracy",
        ):
            row[key_name] = rounded(parsed[key_name])

    return row


def numeric(row: dict[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_voice(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    summary: dict[str, Any] = {
        "voice": first["voice"],
        "gender": first["gender"],
        "locale": first["locale"],
        "cases": len(rows),
        "recognized_cases": sum(1 for row in rows if row["result_reason"] == "recognized"),
        "canceled_cases": sum(1 for row in rows if row["result_reason"] == "canceled"),
        "no_match_cases": sum(1 for row in rows if row["result_reason"] == "no_match"),
    }

    for field in (
        "pronunciation",
        "accuracy",
        "fluency",
        "completeness",
        "word_accuracy",
        "phoneme_accuracy",
        "syllable_accuracy",
        "duration_seconds",
        "peak_dbfs",
        "rms_dbfs",
        "non_silent_seconds",
        "clipping_percent",
    ):
        values = [value for row in rows if (value := numeric(row, field)) is not None]
        output_field = f"{field}_avg" if field != "duration_seconds" else "duration_avg_seconds"
        summary[output_field] = rounded(average(values))

    weighted_values: list[float] = []
    weights: list[float] = []
    for field, weight in COMPOSITE_WEIGHTS.items():
        value = numeric(summary, f"{field}_avg")
        if value is not None:
            weighted_values.append(value * weight)
            weights.append(weight)
    summary["composite_score"] = rounded(sum(weighted_values) / sum(weights) if weights else None)

    return summary


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def print_summary_table(summaries: list[dict[str, Any]]) -> None:
    if not summaries:
        print("No summary rows were produced.")
        return

    sorted_rows = sorted(
        summaries,
        key=lambda row: float(row["composite_score"] or 0),
        reverse=True,
    )
    fields = (
        "voice",
        "gender",
        "composite_score",
        "pronunciation_avg",
        "accuracy_avg",
        "phoneme_accuracy_avg",
        "recognized_cases",
    )
    widths = {
        field: max(len(field), *(len(str(row.get(field, ""))) for row in sorted_rows))
        for field in fields
    }

    print()
    print("Voice summary")
    print(" | ".join(field.ljust(widths[field]) for field in fields))
    print("-+-".join("-" * widths[field] for field in fields))
    for row in sorted_rows:
        print(" | ".join(str(row.get(field, "")).ljust(widths[field]) for field in fields))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score Azure voices against the 44 phonemes.",
    )
    parser.add_argument(
        "--mode",
        choices=("isolated", "word"),
        default="isolated",
        help=(
            "isolated uses SSML phoneme tags for pure sounds; "
            "word uses the PDF sample word for Azure pronunciation assessment."
        ),
    )
    parser.add_argument(
        "--all-voices",
        action="store_true",
        help="Use every male/female en-US Azure voice instead of the default shortlist.",
    )
    parser.add_argument(
        "--voice",
        action="append",
        default=[],
        help="Voice short name to test. Can be used more than once.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit the number of voices after selection. Useful for smoke tests.",
    )
    parser.add_argument(
        "--phoneme-limit",
        type=int,
        default=0,
        help="Limit the number of phoneme cases. Useful for smoke tests.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY_SECONDS,
        help="Seconds to wait between Azure calls.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate audio and assessment JSON even when cached files exist.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear the output folder before running.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_local_env()

    key = os.getenv("AZURE_SPEECH_KEY", "")
    region = os.getenv("AZURE_SPEECH_REGION", "")
    if not key or not region:
        print("Missing Azure Speech config.")
        print("Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in .env or src/api/.env.")
        return 1

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        print("Azure Speech SDK is not installed in this Python environment.")
        print("Try: cd src/api; uv run ../../test/voice_phoneme_scores.py --limit 1 --phoneme-limit 2")
        return 1

    run_output_dir = OUTPUT_DIR / args.mode
    if args.clear and run_output_dir.exists():
        shutil.rmtree(run_output_dir)
    run_output_dir.mkdir(parents=True, exist_ok=True)

    all_voices = list_azure_voices(speechsdk, key, region)
    selected_voices = select_voices(all_voices, args.voice, args.all_voices, args.limit)
    cases = list(PHONEMES)
    if args.phoneme_limit > 0:
        cases = cases[: args.phoneme_limit]

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.speech_recognition_language = ASSESSMENT_LANGUAGE

    print(f"Azure voice phoneme scoring")
    print(f"Mode: {args.mode}")
    print(f"Voices: {len(selected_voices)}")
    print(f"Phoneme cases: {len(cases)}")
    print(f"Output: {run_output_dir}")
    if args.mode == "isolated":
        print(
            "This uses SSML phoneme tags to synthesize isolated sounds. "
            "Azure pronunciation assessment may no-match some isolated consonants."
        )
    else:
        print(
            "This uses sample words from the phoneme PDF, then scores the generated audio "
            "with Azure pronunciation assessment."
        )

    detail_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for voice_index, voice in enumerate(selected_voices, start=1):
        print(f"\n[{voice_index}/{len(selected_voices)}] {voice['name']} ({voice['gender']})")
        voice_rows: list[dict[str, Any]] = []

        for case_index, case in enumerate(cases, start=1):
            print(f"  [{case_index}/{len(cases)}] {case.label} -> {display_source(case, args.mode)}")
            row = score_one_case(
                speechsdk,
                key,
                region,
                speech_config,
                run_output_dir,
                voice,
                case,
                args.mode,
                args.delay,
                args.force,
            )
            voice_rows.append(row)
            detail_rows.append(row)

        summary = summarize_voice(voice_rows)
        summary_rows.append(summary)
        print(
            f"  score={summary['composite_score']} "
            f"pron={summary['pronunciation_avg']} "
            f"phoneme={summary['phoneme_accuracy_avg']}"
        )

        checkpoint_summaries = sorted(
            summary_rows,
            key=lambda row: float(row["composite_score"] or 0),
            reverse=True,
        )
        write_csv(run_output_dir / "voice_summary.csv", SUMMARY_FIELDS, checkpoint_summaries)
        write_csv(run_output_dir / "phoneme_details.csv", DETAIL_FIELDS, detail_rows)

    sorted_summaries = sorted(
        summary_rows,
        key=lambda row: float(row["composite_score"] or 0),
        reverse=True,
    )

    write_csv(run_output_dir / "voice_summary.csv", SUMMARY_FIELDS, sorted_summaries)
    write_csv(run_output_dir / "phoneme_details.csv", DETAIL_FIELDS, detail_rows)
    print_summary_table(sorted_summaries)
    print()
    print(f"Wrote {run_output_dir / 'voice_summary.csv'}")
    print(f"Wrote {run_output_dir / 'phoneme_details.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
