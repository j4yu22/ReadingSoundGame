from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

import httpx

from app.core.config import DIALOGUE_PATH, settings


class DialogueError(RuntimeError):
    pass


def load_dialogue(path: Path = DIALOGUE_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise DialogueError(f"Dialogue file not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def render_template(template: str, variables: dict[str, Any]) -> str:
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", escape(str(value)))
    return rendered


def wrap_ssml(fragment: str, dialogue: dict[str, Any], line: dict[str, Any]) -> str:
    voice = dialogue.get("voice", {})
    voice_name = str(voice.get("name") or settings.azure_speech_voice)
    rate = str(line.get("rate") or voice.get("defaultRate") or "-25%")
    pitch = str(line.get("pitch") or voice.get("defaultPitch") or "-4%")

    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xml:lang="en-US">'
        f'<voice name="{escape(voice_name)}">'
        f'<prosody rate="{escape(rate)}" pitch="{escape(pitch)}">'
        f"{fragment}"
        "</prosody></voice></speak>"
    )


def get_dialogue_line(line_id: str, variables: dict[str, Any]) -> dict[str, str]:
    dialogue = load_dialogue()
    lines = dialogue.get("lines", {})
    line = lines.get(line_id)

    if not isinstance(line, dict):
        raise DialogueError(f"Dialogue line not found: {line_id}")

    text = render_template(str(line.get("text", "")), variables)
    ssml_fragment = render_template(str(line.get("ssml", text)), variables)
    ssml = wrap_ssml(ssml_fragment, dialogue, line)

    return {"text": text, "ssml": ssml}


async def synthesize_azure_speech(ssml: str) -> bytes:
    if not settings.tts_ready:
        raise DialogueError(
            "Azure speech is not configured. Set AZURE_SPEECH_KEY and "
            "AZURE_SPEECH_REGION in src/api/.env or test/arthur/.env."
        )

    url = (
        f"https://{settings.azure_speech_region}.tts.speech.microsoft.com/"
        "cognitiveservices/v1"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": settings.azure_speech_format,
        "User-Agent": "ReadingSoundGame",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, content=ssml.encode("utf-8"), headers=headers)

    if response.status_code >= 400:
        detail = response.text.strip() or f"Azure TTS returned {response.status_code}"
        raise DialogueError(detail)

    return response.content
