from __future__ import annotations

import json
import os
import re
import wave
from io import BytesIO
from typing import Any

from app.core.config import settings
from app.services.text_to_speech import DialogueError


DEFAULT_LANGUAGE = "en-US"
DEFAULT_CORRECTNESS_THRESHOLD = 65.0


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def numeric_score(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def get_assessment(node: dict[str, Any]) -> dict[str, Any]:
    assessment = node.get("PronunciationAssessment")
    if isinstance(assessment, dict):
        return assessment
    return {}


def build_speech_config(speechsdk: Any) -> Any:
    if not settings.azure_speech_key or not settings.azure_speech_region:
        raise DialogueError(
            "Azure speech is not configured. Set AZURE_SPEECH_KEY and "
            "AZURE_SPEECH_REGION in src/api/.env."
        )

    speech_config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key,
        region=settings.azure_speech_region,
    )
    speech_config.speech_recognition_language = os.getenv(
        "AZURE_STT_LANGUAGE",
        DEFAULT_LANGUAGE,
    )
    return speech_config


def parse_json_result(speechsdk: Any, result: Any) -> dict[str, Any]:
    details_json = result.properties.get(
        speechsdk.PropertyId.SpeechServiceResponse_JsonResult
    )

    if not details_json:
        return {}

    try:
        details = json.loads(details_json)
    except json.JSONDecodeError:
        return {}

    if isinstance(details, dict):
        return details

    return {}


def summarize_words(details: dict[str, Any]) -> list[dict[str, Any]]:
    nbest = details.get("NBest")
    if not isinstance(nbest, list) or not nbest:
        return []

    best = nbest[0]
    if not isinstance(best, dict):
        return []

    raw_words = best.get("Words")
    if not isinstance(raw_words, list):
        return []

    words: list[dict[str, Any]] = []

    for raw_word in raw_words:
        if not isinstance(raw_word, dict):
            continue

        word_assessment = get_assessment(raw_word)
        phonemes: list[dict[str, Any]] = []

        for raw_phoneme in raw_word.get("Phonemes") or []:
            if not isinstance(raw_phoneme, dict):
                continue

            phoneme_assessment = get_assessment(raw_phoneme)
            phonemes.append(
                {
                    "phoneme": raw_phoneme.get("Phoneme") or "",
                    "accuracy": numeric_score(
                        phoneme_assessment.get("AccuracyScore")
                    ),
                }
            )

        words.append(
            {
                "word": raw_word.get("Word") or "",
                "accuracy": numeric_score(word_assessment.get("AccuracyScore")),
                "errorType": word_assessment.get("ErrorType") or "None",
                "phonemes": phonemes,
            }
        )

    return words


def overall_assessment(details: dict[str, Any]) -> dict[str, float | None]:
    nbest = details.get("NBest")
    if not isinstance(nbest, list) or not nbest:
        return {}

    best = nbest[0]
    if not isinstance(best, dict):
        return {}

    assessment = get_assessment(best)
    return {
        "accuracy": numeric_score(assessment.get("AccuracyScore")),
        "fluency": numeric_score(assessment.get("FluencyScore")),
        "completeness": numeric_score(assessment.get("CompletenessScore")),
        "pronunciation": numeric_score(assessment.get("PronScore")),
    }


def is_correct(
    expected: str,
    recognized_text: str,
    scores: dict[str, float | None],
) -> bool:
    expected_normalized = normalize_text(expected)
    recognized_normalized = normalize_text(recognized_text)

    if expected_normalized and expected_normalized == recognized_normalized:
        return True

    threshold = float(
        os.getenv("AZURE_STT_CORRECTNESS_THRESHOLD", DEFAULT_CORRECTNESS_THRESHOLD)
    )
    accuracy = scores.get("accuracy")
    completeness = scores.get("completeness")

    if accuracy is None:
        return False

    if completeness is None:
        return accuracy >= threshold

    return accuracy >= threshold and completeness >= 50


def apply_pronunciation_assessment(
    speechsdk: Any,
    recognizer: Any,
    expected: str,
) -> None:
    pronunciation_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=expected,
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


def build_audio_config_from_wav_bytes(speechsdk: Any, audio_bytes: bytes) -> Any:
    try:
        with wave.open(BytesIO(audio_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            pcm_frames = wav_file.readframes(wav_file.getnframes())
    except wave.Error as exc:
        raise DialogueError("The listen-check audio was not a valid WAV file.") from exc

    if channels < 1:
        raise DialogueError("The listen-check WAV file has no audio channels.")

    if sample_width != 2:
        raise DialogueError("The listen-check WAV file must use 16-bit PCM audio.")

    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=sample_rate,
        bits_per_sample=sample_width * 8,
        channels=channels,
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
    push_stream.write(pcm_frames)
    push_stream.close()
    return speechsdk.audio.AudioConfig(stream=push_stream)


def cancellation_details(speechsdk: Any, result: Any) -> str:
    try:
        details = speechsdk.CancellationDetails(result)
    except TypeError:
        details = getattr(result, "cancellation_details", None)

    if not details:
        return "Azure speech recognition was canceled."

    error_details = getattr(details, "error_details", "") or ""
    reason = getattr(details, "reason", "") or "canceled"
    return error_details or str(reason)


def recognize_wav_bytes(
    audio_bytes: bytes,
    *,
    expected: str = "",
    mode: str = "presence",
) -> dict[str, Any]:
    if not audio_bytes:
        raise DialogueError("No audio was provided.")

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:
        raise DialogueError(
            "Azure Speech SDK is not installed. Run `uv sync` in src/api."
        ) from exc

    speech_config = build_speech_config(speechsdk)
    audio_config = build_audio_config_from_wav_bytes(speechsdk, audio_bytes)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )
    assessment_mode = mode == "final" and bool(expected.strip())

    if assessment_mode:
        apply_pronunciation_assessment(speechsdk, recognizer, expected.strip())

    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.Canceled:
        raise DialogueError(cancellation_details(speechsdk, result))

    if result.reason == speechsdk.ResultReason.NoMatch:
        return {
            "mode": mode,
            "expected": expected,
            "recognizedText": "",
            "speechDetected": False,
            "correct": False,
            "scores": {},
            "words": [],
        }

    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        raise DialogueError(f"Unexpected Azure STT result: {result.reason}")

    details = parse_json_result(speechsdk, result)
    recognized_text = (
        str(details.get("DisplayText") or "").strip()
        or str(getattr(result, "text", "") or "").strip()
    )
    scores = overall_assessment(details) if assessment_mode else {}
    words = summarize_words(details)
    speech_detected = bool(normalize_text(recognized_text))
    correct = (
        is_correct(expected, recognized_text, scores)
        if assessment_mode
        else speech_detected
    )

    return {
        "mode": mode,
        "expected": expected,
        "recognizedText": recognized_text,
        "speechDetected": speech_detected,
        "correct": correct,
        "scores": scores,
        "words": words,
    }
