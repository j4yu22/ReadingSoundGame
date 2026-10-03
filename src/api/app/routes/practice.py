from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.security import require_consented_parent
from app.database import get_db
from app.models.accounts import Parent, utcnow
from app.services.account_service import create_attempt, create_practice, get_owned_attempt

router = APIRouter(prefix="/api/practice", tags=["practice"])


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartSession(StrictInput):
    child_id: UUID
    request_id: UUID


class Selection(StrictInput):
    level: str = Field(min_length=1, max_length=10)
    sublevel: str | int | None = None
    section: str = Field(min_length=1, max_length=40)
    exercise: str = Field(min_length=1, max_length=20)
    line: str = Field(min_length=1, max_length=10)


class StartAttempt(StrictInput):
    session_id: UUID
    request_id: UUID
    selection: Selection


class FinishAttempt(StrictInput):
    outcome: Literal["abandoned", "microphone_unavailable"]


@router.post("/sessions")
def start_session(body: StartSession, parent: Parent = Depends(require_consented_parent), db: Session = Depends(get_db)):
    return {"id": create_practice(db, parent, str(body.child_id), str(body.request_id)).id}


@router.post("/attempts")
def start_attempt(body: StartAttempt, parent: Parent = Depends(require_consented_parent), db: Session = Depends(get_db)):
    return {"id": create_attempt(db, parent, str(body.session_id), str(body.request_id), body.selection.model_dump()).id}


@router.post("/attempts/{attempt_id}/finish")
def finish_attempt(attempt_id: UUID, body: FinishAttempt, parent: Parent = Depends(require_consented_parent), db: Session = Depends(get_db)):
    attempt = get_owned_attempt(db, parent, str(attempt_id))
    if attempt.status == "started":
        attempt.status, attempt.finished_at = body.outcome, utcnow()
        db.commit()
    return {"id": attempt.id, "status": attempt.status}
