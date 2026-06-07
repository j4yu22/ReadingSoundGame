from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from app.core.config import SHARED_DIR


router = APIRouter(prefix="/api/activities", tags=["activities"])


@router.get("/current")
async def current_activity(
    type: str = Query("deletion", pattern="^(deletion|substitution)$"),
) -> dict[str, object]:
    path = SHARED_DIR / "activities" / f"{type}.json"

    if not path.is_file():
        raise HTTPException(status_code=404, detail="Activity file not found.")

    data = json.loads(path.read_text(encoding="utf-8"))
    activities = data.get("activities", [])

    if not activities:
        raise HTTPException(status_code=404, detail="No activities found.")

    return activities[0]
