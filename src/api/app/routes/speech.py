from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.config import settings
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
