from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.audio_clips import build_source_tokens, synthesize_token_clip
from app.services.text_to_speech import (
    DialogueError,
    get_dialogue_line,
    load_dialogue,
    synthesize_azure_speech,
)


router = APIRouter(prefix="/api/speech", tags=["speech"])


class DialogueLineRequest(BaseModel):
    line_id: str = Field(..., min_length=1)
    variables: dict[str, Any] = Field(default_factory=dict)


class TokenClipRequest(BaseModel):
    token: str = Field(..., min_length=1)
    source_phrase: str = ""
    tokens: list[str] = Field(default_factory=list)
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
    try:
        rendered = get_dialogue_line(request.line_id, request.variables)
        audio = await synthesize_azure_speech(rendered["ssml"])
    except DialogueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"X-Arthur-Text": rendered["text"]},
    )


@router.post("/token-clip")
async def token_clip(request: TokenClipRequest) -> Response:
    source_tokens = build_source_tokens(request.tokens, request.source_phrase)

    try:
        audio = await asyncio.to_thread(
            synthesize_token_clip,
            source_tokens,
            request.token,
            request.occurrence,
            request.source_phrase,
        )
    except DialogueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "X-Arthur-Text": request.token,
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
