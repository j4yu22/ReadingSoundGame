from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import SESSION_COOKIE, assert_consented, consent_status, csrf_token, require_consented_parent, require_parent, require_recent_parent
from app.database import get_db
from app.models.accounts import Attempt, Child, ConsentRecord, DeletionJob, Parent, PracticeSession, utcnow
from app.routes.practice import StrictInput
from app.services.account_service import attempt_view, child_progress
from app.services.privacy_service import notify, queue_deletion

router = APIRouter(prefix="/api/account", tags=["accounts"])


class ConsentInput(StrictInput):
    notice_version: str = Field(min_length=1, max_length=80)
    adult_confirmed: Literal[True]


class ChildInput(StrictInput):
    nickname: str = Field(min_length=1, max_length=40)

    @field_validator("nickname")
    @classmethod
    def validate_nickname(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Enter a short nickname without control characters.")
        return value


@router.get("/config")
def config():
    return {"enabled": settings.accounts_ready, "registration_open": settings.accounts_ready and settings.account_registration_open, "child_collection_enabled": settings.accounts_ready and settings.child_data_collection_enabled, "practice_mode": settings.practice_mode, "privacy_notice_version": settings.privacy_notice_version, "privacy_contact": settings.privacy_contact, "retention_days": settings.retention_days}


@router.get("/me")
def me(request: Request, parent: Parent = Depends(require_parent), db: Session = Depends(get_db)):
    children = list(db.scalars(select(Child).where(Child.parent_id == parent.id, Child.status == "active").order_by(Child.created_at)))
    return {"id": parent.id, "email": parent.email, "consent_status": consent_status(db, parent), "csrf_token": csrf_token(request.cookies[SESSION_COOKIE]), "children": [{"id": child.id, "nickname": child.nickname} for child in children], "deletion_pending": db.scalar(select(DeletionJob.id).where(DeletionJob.parent_id == parent.id, DeletionJob.status == "pending").limit(1)) is not None}


@router.post("/consent", status_code=202)
def request_consent(body: ConsentInput, parent: Parent = Depends(require_parent), db: Session = Depends(get_db)):
    db.scalar(select(Parent).where(Parent.id == parent.id).with_for_update())
    if body.notice_version != settings.privacy_notice_version:
        raise HTTPException(409, "Read the current privacy notice before requesting consent review.")
    if db.scalar(select(DeletionJob.id).where(DeletionJob.parent_id == parent.id, DeletionJob.status == "pending")):
        raise HTTPException(409, "Wait for the requested deletion to finish.")
    status = consent_status(db, parent)
    if status in {"pending", "verified"}:
        return {"status": status}
    for old in db.scalars(select(ConsentRecord).where(ConsentRecord.parent_id == parent.id, ConsentRecord.status == "verified")):
        old.status = "superseded"
    consent = ConsentRecord(parent_id=parent.id, notice_version=body.notice_version)
    db.add(consent)
    db.flush()
    notify(db, "consent_review_requested", consent.id, parent.email)
    db.commit()
    return {"status": "pending", "reference": consent.id}


@router.post("/consent/withdraw", status_code=202)
def withdraw(parent: Parent = Depends(require_recent_parent), db: Session = Depends(get_db)):
    job = queue_deletion(db, parent, "withdrawal")
    return {"status": "pending", "reference": job.id}


@router.post("/children", status_code=201)
def add_child(body: ChildInput, parent: Parent = Depends(require_consented_parent), db: Session = Depends(get_db)):
    assert_consented(db, parent, lock=True)
    if len(list(db.scalars(select(Child.id).where(Child.parent_id == parent.id)))) >= 10:
        raise HTTPException(409, "This account already has the maximum number of child profiles.")
    child = Child(parent_id=parent.id, nickname=body.nickname)
    db.add(child)
    db.commit()
    return {"id": child.id, "nickname": child.nickname}


@router.delete("/children/{child_id}", status_code=202)
def delete_child(child_id: UUID, parent: Parent = Depends(require_recent_parent), db: Session = Depends(get_db)):
    job = queue_deletion(db, parent, "child", str(child_id))
    return {"status": "pending", "reference": job.id}


@router.get("/children/{child_id}/progress")
def progress(child_id: UUID, parent: Parent = Depends(require_parent), db: Session = Depends(get_db)):
    return child_progress(db, parent, str(child_id))


@router.get("/export")
def export(parent: Parent = Depends(require_recent_parent), db: Session = Depends(get_db)):
    import json
    children = list(db.scalars(select(Child).where(Child.parent_id == parent.id)))
    consent = list(db.scalars(select(ConsentRecord).where(ConsentRecord.parent_id == parent.id)))
    data = {"exported_at": utcnow(), "parent": {"id": parent.id, "email": parent.email, "created_at": parent.created_at}, "consent": [{"notice_version": c.notice_version, "status": c.status, "requested_at": c.created_at, "verified_at": c.verified_at, "withdrawn_at": c.withdrawn_at} for c in consent]}
    def stream():
        yield json.dumps(jsonable_encoder(data))[:-1] + ', "children":['
        for index, child in enumerate(children):
            if index:
                yield ","
            header = {"id": child.id, "nickname": child.nickname, "status": child.status}
            yield json.dumps(header)[:-1] + ', "attempts":['
            query = select(Attempt).join(PracticeSession).where(PracticeSession.child_id == child.id, PracticeSession.parent_id == parent.id).order_by(Attempt.created_at).execution_options(yield_per=100)
            for attempt_index, attempt in enumerate(db.scalars(query)):
                if attempt_index:
                    yield ","
                yield json.dumps(attempt_view(attempt))
            yield "]}"
        yield "]}"
    return StreamingResponse(stream(), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="reading-sound-games-data.json"', "Cache-Control": "no-store"})


@router.delete("", status_code=202)
def delete_account(parent: Parent = Depends(require_recent_parent), db: Session = Depends(get_db)):
    job = queue_deletion(db, parent, "parent")
    return {"status": "pending", "reference": job.id}
