from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILES = (
    REPO_ROOT / ".env",
    REPO_ROOT / "test" / "arthur" / ".env",
    REPO_ROOT / "src" / "api" / ".env",
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


def score_text(score: Any) -> str:
    if isinstance(score, int | float):
        return f"{score:.1f}"
    return "n/a"


def get_assessment(node: dict[str, Any]) -> dict[str, Any]:
    assessment = node.get("PronunciationAssessment")
    if isinstance(assessment, dict):
        return assessment
    return {}


def print_assessment_summary(details: dict[str, Any]) -> None:
    nbest = details.get("NBest")
    if not isinstance(nbest, list) or not nbest:
        print("No detailed pronunciation result returned.")
        return

    best = nbest[0]
    if not isinstance(best, dict):
        print("No detailed pronunciation result returned.")
        return

    print(f"Recognized: {details.get('DisplayText') or best.get('Display') or '[empty]'}")

    overall = get_assessment(best)
    if overall:
        print(
            "Overall: "
            f"accuracy={score_text(overall.get('AccuracyScore'))}, "
            f"fluency={score_text(overall.get('FluencyScore'))}, "
            f"completeness={score_text(overall.get('CompletenessScore'))}, "
            f"pronunciation={score_text(overall.get('PronScore'))}"
        )

    words = best.get("Words")
    if not isinstance(words, list) or not words:
        return

    print("Words / sounds:")
    for word in words:
        if not isinstance(word, dict):
            continue

        word_assessment = get_assessment(word)
        word_text = word.get("Word") or word.get("word") or "[unknown]"
        error_type = word_assessment.get("ErrorType") or "None"
        print(
            f"  {word_text}: "
            f"accuracy={score_text(word_assessment.get('AccuracyScore'))}, "
            f"error={error_type}"
        )

        phonemes = word.get("Phonemes")
        if isinstance(phonemes, list) and phonemes:
            printed = []
            for phoneme in phonemes:
                if not isinstance(phoneme, dict):
                    continue
                phoneme_text = phoneme.get("Phoneme") or "?"
                phoneme_score = get_assessment(phoneme).get("AccuracyScore")
                printed.append(f"{phoneme_text}:{score_text(phoneme_score)}")
            if printed:
                print(f"    phonemes: {'  '.join(printed)}")

        syllables = word.get("Syllables")
        if isinstance(syllables, list) and syllables:
            printed = []
            for syllable in syllables:
                if not isinstance(syllable, dict):
                    continue
                syllable_text = syllable.get("Syllable") or "?"
                syllable_score = get_assessment(syllable).get("AccuracyScore")
                printed.append(f"{syllable_text}:{score_text(syllable_score)}")
            if printed:
                print(f"    syllables: {'  '.join(printed)}")


def assess_once(speechsdk: Any, speech_config: Any, expected: str) -> None:
    audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

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

    print(f"\nExpected: {expected}")
    print("Listening now. Speak once, then pause...")
    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.NoMatch:
        print("No speech matched.")
        return

    if result.reason == speechsdk.ResultReason.Canceled:
        cancellation = speechsdk.CancellationDetails(result)
        print(f"Canceled: {cancellation.reason}")
        if cancellation.error_details:
            print(f"Error details: {cancellation.error_details}")
        return

    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        print(f"Unexpected recognition result: {result.reason}")
        return

    details_json = result.properties.get(
        speechsdk.PropertyId.SpeechServiceResponse_JsonResult
    )
    if not details_json:
        print(f"Recognized: {result.text or '[empty]'}")
        print("No pronunciation JSON returned.")
        return

    try:
        details = json.loads(details_json)
    except json.JSONDecodeError:
        print(f"Recognized: {result.text or '[empty]'}")
        print("Could not parse pronunciation JSON.")
        print(details_json)
        return

    print_assessment_summary(details)

    if os.getenv("AZURE_STT_PRINT_JSON", "").lower() in {"1", "true", "yes"}:
        print("\nRaw JSON:")
        print(json.dumps(details, indent=2))


def main() -> int:
    load_local_env()

    key = os.getenv("AZURE_SPEECH_KEY", "")
    region = os.getenv("AZURE_SPEECH_REGION", "")
    language = os.getenv("AZURE_STT_LANGUAGE", "en-US")

    if not key or not region:
        print("Missing Azure Speech config.")
        print("Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in src/api/.env.")
        return 1

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        print("Azure Speech SDK is not installed in this Python environment.")
        print("Run this from src/api with: uv run ../../test/listen_azure_stt.py")
        return 1

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.speech_recognition_language = language

    print("Azure pronunciation assessment test")
    print(f"Language: {language}")
    print("Type the expected sound, word, or phrase. Leave blank to quit.")
    print("Tip: set AZURE_STT_PRINT_JSON=1 to print Azure's raw result.")

    while True:
        try:
            expected = input("\nExpected> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nStopping.")
            return 0

        if not expected:
            print("Stopping.")
            return 0

        assess_once(speechsdk, speech_config, expected)


if __name__ == "__main__":
    raise SystemExit(main())
