from __future__ import annotations

import os
import re
from pathlib import Path
from xml.sax.saxutils import escape


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


def normalize_phoneme(raw_text: str) -> str:
    phoneme = raw_text.strip()
    phoneme = re.sub(r"^/|/$", "", phoneme)
    phoneme = re.sub(r"^\[|\]$", "", phoneme)
    return phoneme.strip()


def build_ssml(phoneme: str, voice: str, rate: str) -> str:
    escaped_voice = escape(voice, {'"': "&quot;"})
    escaped_phoneme = escape(phoneme, {'"': "&quot;"})
    body_text = escape(phoneme)

    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xml:lang="en-US">'
        f'<voice name="{escaped_voice}">'
        f'<prosody rate="{escape(rate)}">'
        f'<phoneme alphabet="ipa" ph="{escaped_phoneme}">{body_text}</phoneme>'
        "</prosody>"
        "</voice>"
        "</speak>"
    )


def speak_phoneme(speechsdk: object, speech_config: object, phoneme: str) -> None:
    voice = os.getenv("AZURE_SPEECH_VOICE", "en-US-GuyNeural")
    rate = os.getenv("AZURE_PHONEME_RATE", "-35%")

    speech_config.speech_synthesis_voice_name = voice
    audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    ssml = build_ssml(phoneme, voice, rate)
    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        print(f"Spoke IPA phoneme: /{phoneme}/")
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
        print("Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in src/api/.env.")
        return 1

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        print("Azure Speech SDK is not installed in this Python environment.")
        print("Run this from src/api with: uv run ../../test/say_azure_phoneme.py")
        return 1

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)

    print("Azure IPA phoneme speaker")
    print("Type an IPA phoneme, with or without slashes. Leave blank to quit.")
    print("Example inputs: s, k, ae, sh, t")
    print("Set AZURE_PHONEME_RATE to change speed, default is -35%.")

    while True:
        try:
            raw_text = input("\nIPA> ")
        except (EOFError, KeyboardInterrupt):
            print("\nStopping.")
            return 0

        phoneme = normalize_phoneme(raw_text)
        if not phoneme:
            print("Stopping.")
            return 0

        speak_phoneme(speechsdk, speech_config, phoneme)


if __name__ == "__main__":
    raise SystemExit(main())
