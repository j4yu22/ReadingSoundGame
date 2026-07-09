from __future__ import annotations

import os
from pathlib import Path
from xml.sax.saxutils import escape

# Tuning controls
VOICE_NAME = os.getenv("AZURE_TEXT_TEST_VOICE", "en-US-ChristopherNeural")
VOICE_LOCALE = "en-US"
USE_SSML = True

# Azure prosody values. Examples:
# RATE: "-30%", "0%", "+10%"
# PITCH: "-20%", "0%", "+20%", "low", "medium", "high"
# VOLUME: "-20%", "+0%", "+20%"
RATE = "0%"
PITCH = "+15%"
VOLUME = "+10%"


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


def build_ssml(text: str) -> str:
    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{escape(VOICE_LOCALE)}">'
        f'<voice name="{escape(VOICE_NAME, {chr(34): "&quot;"})}">'
        f'<prosody rate="{escape(RATE)}" pitch="{escape(PITCH)}" '
        f'volume="{escape(VOLUME)}">'
        f"{escape(text)}"
        "</prosody>"
        "</voice>"
        "</speak>"
    )


def speak_text(speechsdk: object, speech_config: object, text: str) -> None:
    speech_config.speech_synthesis_voice_name = VOICE_NAME
    audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    if USE_SSML:
        result = synthesizer.speak_ssml_async(build_ssml(text)).get()
    else:
        result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        print(f'Spoke: "{text}"')
        return

    if result.reason == speechsdk.ResultReason.Canceled:
        details = speechsdk.SpeechSynthesisCancellationDetails(result)
        print(f"Canceled: {details.reason}")
        if details.error_details:
            print(f"Error details: {details.error_details}")
        return

    print(f"Unexpected synthesis result: {result.reason}")


def main() -> int:
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

    print("Arthur plain text speaker")
    print(f"Voice: {VOICE_NAME}")
    if USE_SSML:
        print(f"Rate: {RATE} | Pitch: {PITCH} | Volume: {VOLUME}")
        print("Mode: SSML text with prosody controls")
    else:
        print("Mode: plain Azure text, no SSML/prosody")
    print("Type text and press Enter. Leave blank to quit.")

    while True:
        try:
            text = input("\ntext> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nStopping.")
            return 0

        if not text:
            print("Stopping.")
            return 0

        speak_text(speechsdk, speech_config, text)


if __name__ == "__main__":
    raise SystemExit(main())
