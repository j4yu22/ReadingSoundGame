from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from html import escape
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles


APP_DIR = Path(__file__).resolve().parent


def load_local_env() -> None:
    env_path = APP_DIR / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


load_local_env()

FCC_REPO = Path(
    os.getenv("FCC_REPO_PATH", r"O:\coding\python\gitstuff\free-claude-code")
)

if FCC_REPO.is_dir() and str(FCC_REPO) not in sys.path:
    sys.path.append(str(FCC_REPO))

FCC_BASE_URL = os.getenv("FCC_BASE_URL", "http://127.0.0.1:8082").rstrip("/")
FCC_AUTH_TOKEN = os.getenv("FCC_AUTH_TOKEN", os.getenv("ANTHROPIC_AUTH_TOKEN", "freecc"))
FCC_MODEL = os.getenv("FCC_MODEL", "claude-3-5-sonnet-20241022")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
HF_TOKEN = os.getenv("HF_TOKEN", "")
NVIDIA_NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY", "")
TTS_PROVIDER = os.getenv("ARTHUR_TTS_PROVIDER", "browser").strip().lower()
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "")
AZURE_SPEECH_VOICE = os.getenv("AZURE_SPEECH_VOICE", "en-US-GuyNeural")
AZURE_SPEECH_FORMAT = os.getenv(
    "AZURE_SPEECH_FORMAT", "audio-24khz-48kbitrate-mono-mp3"
)
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_HISTORY_MESSAGES = 14

SYSTEM_PROMPT = os.getenv(
    "ARTHUR_SYSTEM_PROMPT",
    (
        "You are Arthur, a warm male-voiced AI conversation partner. "
        "Never narrate your reasoning, analysis, uncertainty, or interpretation process. "
        "Never say phrases like 'the user said', 'I need to', 'we need to', "
        "'probably', 'likely', or 'return JSON'. "
        "Have a natural spoken conversation. Be concise by default, but answer more "
        "fully when the user asks for detail. "
        "Return only JSON with two string fields: display_text and speech_ssml. "
        "Do not include markdown fences, comments, explanations, or any text outside the JSON. "
        "display_text is what the user reads and hears. speech_ssml is usually empty. "
        "Only provide complete Azure-compatible SSML when the user explicitly asks for "
        "exact pronunciation, phonetics, or custom spoken delivery."
    ),
)

app = FastAPI(title="Arthur Voice Chat")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5177",
        "http://localhost:5177",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-arthur-session"],
)
app.mount("/static", StaticFiles(directory=APP_DIR), name="static")

sessions: dict[str, list[dict[str, str]]] = {}
session_lock = asyncio.Lock()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(APP_DIR / "arthur.html")


@app.get("/arthur.css")
async def css() -> FileResponse:
    return FileResponse(APP_DIR / "arthur.css", media_type="text/css")


@app.get("/arthur.js")
async def js() -> FileResponse:
    return FileResponse(APP_DIR / "arthur.js", media_type="text/javascript")


@app.get("/recorder-worklet.js")
async def recorder_worklet() -> FileResponse:
    return FileResponse(
        APP_DIR / "recorder-worklet.js", media_type="text/javascript"
    )


@app.get("/api/config")
async def config() -> dict[str, Any]:
    proxy_ok = False
    proxy_error = ""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{FCC_BASE_URL}/health")
            proxy_ok = response.status_code < 400
    except Exception as exc:  # noqa: BLE001 - returned as UI status
        proxy_error = str(exc)

    return {
        "fcc_base_url": FCC_BASE_URL,
        "fcc_model": FCC_MODEL,
        "proxy_ok": proxy_ok,
        "proxy_error": proxy_error,
        "whisper_device": WHISPER_DEVICE,
        "whisper_model": WHISPER_MODEL,
        "tts_provider": TTS_PROVIDER,
        "tts_ready": tts_is_ready(),
        "tts_voice": AZURE_SPEECH_VOICE if TTS_PROVIDER == "azure" else "browser",
    }


@app.post("/api/tts")
async def tts(request: Request) -> Response:
    payload = await request.json()
    text = str(payload.get("text", "")).strip()
    ssml = str(payload.get("ssml", "")).strip()

    if not text and not ssml:
        raise HTTPException(status_code=400, detail="No text received for TTS.")
    if TTS_PROVIDER != "azure":
        raise HTTPException(status_code=400, detail="Server TTS is not configured.")
    if not tts_is_ready():
        raise HTTPException(
            status_code=500,
            detail="Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION for Azure TTS.",
        )

    audio = await synthesize_azure_speech(ssml or text_to_ssml(text))
    return Response(content=audio, media_type="audio/mpeg")


@app.post("/api/reset")
async def reset(x_arthur_session: str = Header(default="default")) -> dict[str, str]:
    async with session_lock:
        sessions.pop(x_arthur_session, None)
    return {"status": "reset"}


@app.post("/api/turn")
async def turn(
    request: Request,
    x_arthur_session: str = Header(default="default"),
) -> JSONResponse:
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="No audio received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio is over 25 MB.")

    content_type = request.headers.get("content-type", "audio/wav").split(";")[0]
    user_text = await transcribe_audio_bytes(audio, content_type)
    if not user_text or user_text == "(no speech detected)":
        raise HTTPException(status_code=400, detail="No speech detected.")

    async with session_lock:
        history = sessions.setdefault(x_arthur_session, [])
        messages = [*history, {"role": "user", "content": user_text}]
        messages = messages[-MAX_HISTORY_MESSAGES:]

    assistant = await ask_free_claude_code(messages)
    assistant_text = assistant["display_text"]

    async with session_lock:
        history = sessions.setdefault(x_arthur_session, [])
        history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
        )
        del history[:-MAX_HISTORY_MESSAGES]

    return JSONResponse(
        {
            "user_text": user_text,
            "assistant_text": assistant_text,
            "assistant_ssml": assistant["speech_ssml"],
        }
    )


async def transcribe_audio_bytes(audio: bytes, content_type: str) -> str:
    suffix = ".wav"
    if content_type == "audio/mpeg":
        suffix = ".mp3"
    elif content_type in {"audio/mp4", "audio/x-m4a"}:
        suffix = ".m4a"
    elif content_type in {"audio/ogg", "application/ogg"}:
        suffix = ".ogg"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(audio)
        temp_path = Path(temp_file.name)

    try:
        from messaging.transcription import transcribe_audio

        return await asyncio.to_thread(
            transcribe_audio,
            temp_path,
            content_type,
            whisper_model=WHISPER_MODEL,
            whisper_device=WHISPER_DEVICE,
            hf_token=HF_TOKEN,
            nvidia_nim_api_key=NVIDIA_NIM_API_KEY,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Local Whisper dependencies are missing. Install the "
                "free-claude-code voice_local extra, or set WHISPER_DEVICE=nvidia_nim "
                "with the voice extra and NVIDIA_NIM_API_KEY."
            ),
        ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def tts_is_ready() -> bool:
    if TTS_PROVIDER == "browser":
        return True
    if TTS_PROVIDER == "azure":
        return bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)
    return False


def text_to_ssml(text: str) -> str:
    if text.lstrip().startswith("<speak"):
        return text

    escaped = escape(text)
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
        f'<voice name="{escape(AZURE_SPEECH_VOICE)}">{escaped}</voice>'
        "</speak>"
    )


async def synthesize_azure_speech(ssml: str) -> bytes:
    url = (
        f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/"
        "cognitiveservices/v1"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": AZURE_SPEECH_FORMAT,
        "User-Agent": "ArthurVoiceChat",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, content=ssml.encode("utf-8"), headers=headers)

    if response.status_code >= 400:
        detail = response.text.strip() or f"Azure TTS returned {response.status_code}"
        raise HTTPException(status_code=502, detail=detail)
    return response.content


async def ask_free_claude_code(messages: list[dict[str, str]]) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if FCC_AUTH_TOKEN:
        headers["authorization"] = f"Bearer {FCC_AUTH_TOKEN}"

    payload = {
        "model": FCC_MODEL,
        "max_tokens": 220,
        "temperature": 0.2,
        "system": SYSTEM_PROMPT,
        "messages": messages,
        "stream": True,
        "thinking": {"type": "disabled", "enabled": False},
    }

    chunks: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=8.0)) as client:
            async with client.stream(
                "POST",
                f"{FCC_BASE_URL}/v1/messages",
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    raise HTTPException(
                        status_code=502,
                        detail=detail.decode("utf-8", errors="replace"),
                    )

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        collect_sse_text(line[6:], chunks)
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Free Claude Code proxy is not reachable at {FCC_BASE_URL}.",
        ) from exc

    text = "".join(chunks).strip()
    if not text:
        raise HTTPException(status_code=502, detail="The model returned no text.")
    return parse_assistant_payload(text)


def parse_assistant_payload(text: str) -> dict[str, str]:
    raw = extract_json_object(text.strip())

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        extracted_display = extract_json_string_field(text, "display_text")
        extracted_ssml = extract_json_string_field(text, "speech_ssml")
        if extracted_display:
            return {
                "display_text": extracted_display,
                "speech_ssml": extracted_ssml,
            }
        return {
            "display_text": sanitize_fallback_response(text),
            "speech_ssml": "",
        }

    if not isinstance(payload, dict):
        return {"display_text": text, "speech_ssml": ""}

    display_text = str(payload.get("display_text") or "").strip()
    speech_ssml = str(payload.get("speech_ssml") or "").strip()
    return {
        "display_text": display_text or sanitize_fallback_response(text),
        "speech_ssml": speech_ssml,
    }


def extract_json_object(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            _, end = decoder.raw_decode(raw[match.start() :])
        except json.JSONDecodeError:
            continue
        return raw[match.start() : match.start() + end]
    return raw


def extract_json_string_field(text: str, field_name: str) -> str:
    pattern = rf'"{re.escape(field_name)}"\s*:\s*"((?:\\.|[^"\\])*)'
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""

    raw_value = match.group(1)
    try:
        return json.loads(f'"{raw_value}"').strip()
    except json.JSONDecodeError:
        return raw_value.replace(r"\"", '"').replace(r"\\", "\\").strip()


def sanitize_fallback_response(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    reasoning_markers = (
        "okay, the user",
        "the user is",
        "let me",
        "i need to",
        "probably",
        "likely",
        "looking at the pattern",
        "my response",
        "we need to",
        "return json",
        "display_text",
        "speech_ssml",
        "thus:",
        "azure-compatible",
        "complete ssml",
        "provide ssml",
    )
    if any(marker in lowered for marker in reasoning_markers):
        return "Sorry, I got tangled there. What would you like to talk about?"
    return stripped


def collect_sse_text(data: str, chunks: list[str]) -> None:
    if not data or data == "[DONE]":
        return

    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return

    event_type = event.get("type")
    if event_type == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            chunks.append(delta["text"])
    elif event_type == "content_block_start":
        content_block = event.get("content_block") or {}
        if content_block.get("type") == "text" and isinstance(
            content_block.get("text"), str
        ):
            chunks.append(content_block["text"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=5177, reload=False)
