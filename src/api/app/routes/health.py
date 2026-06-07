from __future__ import annotations

from fastapi import APIRouter

from app.core.config import DIALOGUE_PATH, settings


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "tts_ready": settings.tts_ready,
        "tts_voice": settings.azure_speech_voice,
        "dialogue_ready": DIALOGUE_PATH.is_file(),
    }
