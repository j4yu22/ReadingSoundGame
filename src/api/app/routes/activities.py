from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.core.config import SHARED_DIR
from app.services.activity_breakdown import clip_path_for_id, prepare_activity
from app.services.activity_service import (
    CATALOG_PATH,
    ActivityCatalogError,
    select_catalog_activity,
)
from app.services.text_to_speech import DialogueError


router = APIRouter(prefix="/api/activities", tags=["activities"])


@router.get("/catalog")
async def activity_catalog() -> FileResponse:
    if not CATALOG_PATH.is_file():
        raise HTTPException(status_code=404, detail="Exercise catalog not found.")

    return FileResponse(
        CATALOG_PATH,
        media_type="application/json",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/current")
async def current_activity(
    type: str = Query("deletion", pattern="^(deletion|substitution)$"),
    level: str | None = Query(None),
    sublevel: str | None = Query(None),
    section: str | None = Query(None),
    exercise_number: str | None = Query(None, alias="exercise"),
    line_letter: str | None = Query(None, alias="line"),
) -> dict[str, object]:
    if level is not None:
        if not section or not exercise_number or not line_letter:
            raise HTTPException(
                status_code=400,
                detail="Catalog activities require level, section, exercise, and line.",
            )

        try:
            activity = select_catalog_activity(
                level=level,
                sublevel=sublevel,
                section=section,
                exercise_number=exercise_number,
                line_letter=line_letter,
            )
        except ActivityCatalogError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        activity_type = str(activity.get("type") or "").strip().lower()
        try:
            return await asyncio.to_thread(prepare_activity, activity, activity_type)
        except DialogueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    path = SHARED_DIR / "activities" / f"{type}.json"

    if not path.is_file():
        raise HTTPException(status_code=404, detail="Activity file not found.")

    data = json.loads(path.read_text(encoding="utf-8"))
    activities = data.get("activities", [])

    if not activities:
        raise HTTPException(status_code=404, detail="No activities found.")

    try:
        return await asyncio.to_thread(prepare_activity, activities[0], type)
    except DialogueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/clips/{clip_id}")
async def activity_clip(clip_id: str) -> Response:
    try:
        path = clip_path_for_id(clip_id)
    except DialogueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(
        content=path.read_bytes(),
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
