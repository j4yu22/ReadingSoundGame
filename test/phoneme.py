from __future__ import annotations

import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

# Tuning controls
VOICE_NAME = os.getenv("AZURE_TEXT_TEST_VOICE", "en-US-AvaNeural")
VOICE_LOCALE = "en-US"
PHONEME_ALPHABET = "ipa"

# Text inside the phoneme tag is only a fallback label. Azure should pronounce
# the IPA value supplied in the tag's `ph` attribute instead.
PHONEME_PLACEHOLDER = "sound"

# Azure prosody values. Examples:
# RATE: "-30%", "0%", "+10%"
# PITCH: "-20%", "0%", "+20%", "low", "medium", "high"
# VOLUME: "-20%", "+0%", "+20%"
RATE = "0%"
PITCH = "+15%"
VOLUME = "+10%"

# Small silences keep short consonants from being clipped at playback edges.
LEADING_BREAK_MS = 100
TRAILING_BREAK_MS = 250


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


def configure_console_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def normalize_phoneme(value: str) -> str:
    phoneme = value.strip()
    if len(phoneme) >= 2 and (
        (phoneme.startswith("/") and phoneme.endswith("/"))
        or (phoneme.startswith("[") and phoneme.endswith("]"))
    ):
        phoneme = phoneme[1:-1].strip()
    return phoneme


def build_ssml(phoneme: str) -> str:
    escaped_voice = escape(VOICE_NAME, {'"': "&quot;"})
    escaped_phoneme = escape(phoneme, {'"': "&quot;"})
    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{escape(VOICE_LOCALE)}">'
        f'<voice name="{escaped_voice}">'
        f'<prosody rate="{escape(RATE)}" pitch="{escape(PITCH)}" '
        f'volume="{escape(VOLUME)}">'
        f'<break time="{LEADING_BREAK_MS}ms"/>'
        f'<phoneme alphabet="{escape(PHONEME_ALPHABET)}" '
        f'ph="{escaped_phoneme}">{escape(PHONEME_PLACEHOLDER)}</phoneme>'
        f'<break time="{TRAILING_BREAK_MS}ms"/>'
        "</prosody>"
        "</voice>"
        "</speak>"
    )


def speak_phoneme(speechsdk: object, speech_config: object, phoneme: str) -> None:
    speech_config.speech_synthesis_voice_name = VOICE_NAME
    audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    result = synthesizer.speak_ssml_async(build_ssml(phoneme)).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        print(f'Spoke IPA: /{phoneme}/')
        return

    if result.reason == speechsdk.ResultReason.Canceled:
        details = speechsdk.SpeechSynthesisCancellationDetails(result)
        print(f"Canceled: {details.reason}")
        if details.error_details:
            print(f"Error details: {details.error_details}")
        return

    print(f"Unexpected synthesis result: {result.reason}")


def main() -> int:
    configure_console_utf8()
    load_local_env()

    key = os.getenv("AZURE_SPEECH_KEY", "")
    region = os.getenv("AZURE_SPEECH_REGION", "")

    if not key or not region:
        print("Missing Azure Speech config.")
        print("Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in one of these files:")
        for path in ENV_FILES:
            print(f"  {path}")
        return 1

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        print("Azure Speech SDK is not installed in this Python environment.")
        print("Recommended command:")
        print("  cd src/api")
        print("  uv run ../../test/phoneme.py")
        return 1

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)

    print("Arthur IPA phoneme speaker")
    print(f"Voice: {VOICE_NAME}")
    print(f"Rate: {RATE} | Pitch: {PITCH} | Volume: {VOLUME}")
    print(f"Mode: SSML phoneme tags using {PHONEME_ALPHABET.upper()}")
    print("Enter IPA with or without slashes, such as /s/, /m/, /ʃ/, /tʃ/, or /æ/.")
    print("Leave blank to quit.")

    while True:
        try:
            raw_phoneme = input("\nphoneme> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nStopping.")
            return 0

        if not raw_phoneme:
            print("Stopping.")
            return 0

        phoneme = normalize_phoneme(raw_phoneme)
        if not phoneme:
            print("Enter a phoneme, such as /s/ or /ʃ/.")
            continue

        speak_phoneme(speechsdk, speech_config, phoneme)


if __name__ == "__main__":
    raise SystemExit(main())
