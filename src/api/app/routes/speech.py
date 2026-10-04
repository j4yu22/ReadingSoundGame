from __future__ import annotations

import asyncio
import math
import wave
from io import BytesIO
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import get_practice_db, require_practice_parent
from app.services.account_service import (
    begin_speech_check,
    expected_for_attempt,
    get_owned_attempt,
    record_speech_error,
    record_speech_result,
)
from app.services.audio_clips import build_source_tokens, synthesize_token_clip
from app.services.speech_to_text import recognize_wav_bytes
from app.services.text_to_speech import (
    DialogueError,
    get_dialogue_line,
    load_dialogue,
    synthesize_azure_speech,
)


router = APIRouter(
    prefix="/api/speech", tags=["speech"],
    dependencies=[Depends(require_practice_parent)],
)
MAX_AUDIO_BYTES = 512 * 1024
MAX_AUDIO_SECONDS = 5


class DialogueLineRequest(BaseModel):
    line_id: str = Field(..., min_length=1, max_length=64)
    variables: dict[str, Any] = Field(default_factory=dict)


class TokenClipRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=80)
    source_phrase: str = Field(default="", max_length=320)
    tokens: list[str] = Field(default_factory=list, max_length=20)
    occurrence: int = Field(default=-1, ge=-1)


@router.get("/config")
async def config() -> dict[str, object]:
    dialogue = load_dialogue()
    voice = dialogue.get("voice", {})

    return {
        "tts_ready": settings.tts_ready,
        "voice": voice.get("name") or settings.azure_speech_voice,
        "default_rate": voice.get("defaultRate"),
        "default_pitch": voice.get("defaultPitch"),
    }


@router.post("/line")
async def line(request: DialogueLineRequest) -> Response:
    if len(request.variables) > 12 or any(
        not isinstance(value, (str, int, float)) or len(str(value)) > 160
        for value in request.variables.values()
    ):
        raise HTTPException(status_code=400, detail="Invalid dialogue variables.")
    try:
        rendered = get_dialogue_line(request.line_id, request.variables)
        audio = await synthesize_azure_speech(rendered["ssml"])
    except (DialogueError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Speech playback is temporarily unavailable.") from None

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"X-Arthur-Text": rendered["text"]},
    )


@router.post("/token-clip")
async def token_clip(request: TokenClipRequest) -> Response:
    if any(len(token) > 80 for token in request.tokens):
        raise HTTPException(status_code=400, detail="Invalid sound token.")
    source_tokens = build_source_tokens(request.tokens, request.source_phrase)

    try:
        audio = await asyncio.to_thread(
            synthesize_token_clip,
            source_tokens,
            request.token,
            request.occurrence,
            request.source_phrase,
            cache=False,
        )
    except DialogueError as exc:
        raise HTTPException(status_code=503, detail="Speech playback is temporarily unavailable.") from None

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "X-Arthur-Text": request.token,
            "Cache-Control": "no-store",
        },
    )


@router.post("/listen-check")
async def listen_check(
    request: Request,
    attempt_id: str | None = None,
    mode: str = "presence",
    parent=Depends(require_practice_parent),
    db=Depends(get_practice_db),
    expected: str = "",
) -> dict[str, Any]:
    if mode not in {"presence", "final"}:
        raise HTTPException(status_code=400, detail="Unsupported listen-check mode.")

    guest = not settings.accounts_enabled
    if guest:
        # This transient legacy answer is used only for this speech response.
        # No account, attempt, score, audio, or transcript is saved in guest mode.
        if len(expected) > 320 or any(ord(character) < 32 for character in expected):
            raise HTTPException(status_code=400, detail="Invalid practice answer.")
    else:
        if not attempt_id:
            raise HTTPException(status_code=422, detail="A practice attempt is required.")
        # Account answers come from the stored catalog, never browser input.
        attempt = get_owned_attempt(db, parent, attempt_id)
        expected = expected_for_attempt(attempt, mode)
        # Release the parent lock during upload. The claim below rechecks
        # consent/deletion and acquires a new lock before calling the provider.
        db.commit()
    if request.headers.get("content-type", "").split(";", 1)[0] != "audio/wav":
        raise HTTPException(status_code=415, detail="A WAV recording is required.")
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, detail="Recording is too large.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid recording length.") from None
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Recording is too large.")
        chunks.extend(chunk)
    audio = bytes(chunks)
    chunks.clear()
    try:
        with wave.open(BytesIO(audio), "rb") as recording:
            if (
                recording.getnchannels() != 1
                or recording.getsampwidth() != 2
                or recording.getframerate() != 16000
                or recording.getnframes() <= 0
                or recording.getnframes() > 16000 * MAX_AUDIO_SECONDS
                or len(recording.readframes(recording.getnframes())) != recording.getnframes() * 2
            ):
                raise ValueError
    except (wave.Error, EOFError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or empty recording.") from None

    if not guest:
        begin_speech_check(db, parent, attempt_id, mode)
    try:
        result = await asyncio.to_thread(
            recognize_wav_bytes,
            audio,
            expected=expected,
            mode=mode,
        )
    except Exception:
        if not guest:
            record_speech_error(db, parent, attempt_id, mode)
        raise HTTPException(status_code=503, detail="Speech checking is temporarily unavailable. This does not count as an incorrect answer.") from None
    finally:
        # No recording is written to disk, included in logs, or retained for analytics.
        audio = b""

    public_result = {
        "mode": mode,
        "speechDetected": bool(result.get("speechDetected")),
        "correct": bool(result.get("correct")),
        "scores": {
            name: float(value)
            for name, value in result.get("scores", {}).items()
            if name in {"accuracy", "fluency", "completeness", "pronunciation"}
            and isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 100
        },
    }
    # The helper rechecks live consent/deletion status after the provider call.
    if not guest:
        record_speech_result(db, parent, attempt_id, mode, public_result)
    return public_result
