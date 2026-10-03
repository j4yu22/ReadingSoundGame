from __future__ import annotations

import hashlib
import json
import math
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import assert_consented
from app.models.accounts import ActivityVersion, Attempt, Child, Parent, PracticeSession, SpeechCheck, utcnow
from app.services.activity_service import ActivityCatalogError, select_catalog_activity


def owned_child(db: Session, parent: Parent, child_id: str, *, active: bool = True) -> Child:
    child = db.scalar(select(Child).where(Child.id == child_id, Child.parent_id == parent.id).execution_options(populate_existing=True))
    if not child or (active and child.status != "active"):
        raise HTTPException(404, "Child profile is unavailable.")
    return child


def get_owned_attempt(db: Session, parent: Parent, attempt_id: str) -> Attempt:
    assert_consented(db, parent, lock=True)
    attempt = db.scalar(select(Attempt).join(PracticeSession).where(Attempt.id == attempt_id, PracticeSession.parent_id == parent.id).execution_options(populate_existing=True))
    if not attempt:
        raise HTTPException(404, "Practice attempt is unavailable.")
    practice = db.get(PracticeSession, attempt.session_id, populate_existing=True)
    owned_child(db, parent, practice.child_id)
    if practice.created_at < utcnow() - timedelta(hours=8):
        raise HTTPException(409, "Start a new practice session.")
    return attempt


def expected_for_attempt(attempt: Attempt, mode: str) -> str:
    if mode not in {"presence", "final"}:
        raise HTTPException(400, "Unsupported speech check.")
    key = "answer" if mode == "final" else "word"
    return str(attempt.activity_snapshot.get(key, ""))


def create_practice(db: Session, parent: Parent, child_id: str, request_id: str) -> PracticeSession:
    assert_consented(db, parent, lock=True)
    owned_child(db, parent, child_id)
    existing = db.scalar(select(PracticeSession).where(PracticeSession.parent_id == parent.id, PracticeSession.request_id == request_id))
    if existing:
        if existing.child_id != child_id:
            raise HTTPException(409, "Request ID was already used for a different child.")
        return existing
    recent_count = db.scalar(select(func.count()).select_from(PracticeSession).where(PracticeSession.parent_id == parent.id, PracticeSession.created_at > utcnow() - timedelta(days=1)))
    if recent_count >= 100:
        raise HTTPException(429, "Daily practice session limit reached.")
    practice = PracticeSession(parent_id=parent.id, child_id=child_id, request_id=request_id)
    db.add(practice)
    db.commit()
    return practice


def create_attempt(db: Session, parent: Parent, session_id: str, request_id: str, selection: dict) -> Attempt:
    assert_consented(db, parent, lock=True)
    practice = db.scalar(select(PracticeSession).where(PracticeSession.id == session_id, PracticeSession.parent_id == parent.id))
    if not practice:
        raise HTTPException(404, "Practice session is unavailable.")
    owned_child(db, parent, practice.child_id)
    if practice.created_at < utcnow() - timedelta(hours=8):
        raise HTTPException(409, "Start a new practice session.")
    try:
        snapshot = select_catalog_activity(level=selection["level"], sublevel=selection.get("sublevel"), section=selection["section"], exercise_number=selection["exercise"], line_letter=selection["line"])
    except ActivityCatalogError:
        raise HTTPException(404, "Exercise is unavailable.") from None
    serialized = json.dumps({"activity": snapshot, "scoring": "azure-final-v1"}, sort_keys=True, separators=(",", ":"))
    version_id = hashlib.sha256(serialized.encode()).hexdigest()
    existing = db.scalar(select(Attempt).where(Attempt.session_id == session_id, Attempt.request_id == request_id))
    if existing:
        if existing.activity_version_id != version_id:
            raise HTTPException(409, "Request ID was already used for a different exercise.")
        return existing
    recent_count = db.scalar(select(func.count()).select_from(Attempt).join(PracticeSession).where(PracticeSession.parent_id == parent.id, Attempt.created_at > utcnow() - timedelta(days=1)))
    if recent_count >= 500:
        raise HTTPException(429, "Daily practice attempt limit reached.")
    version = db.get(ActivityVersion, version_id)
    if version is None:
        # Another parent can start the same activity concurrently.
        try:
            with db.begin_nested():
                version = ActivityVersion(id=version_id, catalog_id=snapshot["id"], snapshot=snapshot)
                db.add(version)
                db.flush()
        except IntegrityError:
            version = db.get(ActivityVersion, version_id)
    attempt = Attempt(session_id=session_id, request_id=request_id, activity_version_id=version_id)
    db.add(attempt)
    db.commit()
    return attempt


def begin_speech_check(db: Session, parent: Parent, attempt_id: str, mode: str) -> Attempt:
    attempt = get_owned_attempt(db, parent, attempt_id)
    expected_for_attempt(attempt, mode)
    if attempt.status != "started":
        raise HTTPException(409, "This attempt is already finished.")
    if mode == "final":
        initial = db.scalar(select(SpeechCheck).where(SpeechCheck.attempt_id == attempt.id, SpeechCheck.mode == "presence", SpeechCheck.status == "complete"))
        if not initial or not attempt.speech_detected:
            raise HTTPException(409, "Complete the initial speaking step first.")
    if db.scalar(select(SpeechCheck.id).where(SpeechCheck.attempt_id == attempt.id, SpeechCheck.mode == mode)):
        raise HTTPException(409, "This speaking step has already been submitted.")
    db.add(SpeechCheck(attempt_id=attempt.id, mode=mode))
    db.commit()
    return attempt


def safe_scores(result: dict) -> dict:
    source = result.get("scores") or {}
    return {key: round(float(source[key]), 2) for key in ("accuracy", "fluency", "completeness", "pronunciation") if isinstance(source.get(key), (int, float)) and not isinstance(source[key], bool) and math.isfinite(source[key]) and 0 <= source[key] <= 100}


def record_speech_result(db: Session, parent: Parent, attempt_id: str, mode: str, result: dict) -> None:
    attempt = get_owned_attempt(db, parent, attempt_id)
    check = db.scalar(select(SpeechCheck).where(SpeechCheck.attempt_id == attempt_id, SpeechCheck.mode == mode).execution_options(populate_existing=True))
    if not check or check.status != "processing" or attempt.status != "started":
        raise HTTPException(409, "This speaking step is no longer active.")
    check.status = "complete"
    attempt.speech_detected = result.get("speechDetected") is True
    if not attempt.speech_detected:
        attempt.status, attempt.correct, attempt.finished_at = "no_speech", None, utcnow()
    elif mode == "final":
        attempt.correct = result.get("correct") is True
        attempt.status = "correct" if attempt.correct else "incorrect"
        attempt.scores = safe_scores(result)
        attempt.finished_at = utcnow()
    # Presence means speech was detected; it must never increment correct answers.
    db.commit()


def record_speech_error(db: Session, parent: Parent, attempt_id: str, mode: str) -> None:
    attempt = get_owned_attempt(db, parent, attempt_id)
    check = db.scalar(select(SpeechCheck).where(SpeechCheck.attempt_id == attempt_id, SpeechCheck.mode == mode))
    if check and check.status == "processing" and attempt.status == "started":
        check.status = "failed"
        attempt.status, attempt.correct, attempt.finished_at = "technical_error", None, utcnow()
        db.commit()


def attempt_view(attempt: Attempt) -> dict:
    catalog = attempt.activity_snapshot["catalog"]
    return {"id": attempt.id, "created_at": attempt.created_at.isoformat() + "Z", "finished_at": attempt.finished_at.isoformat() + "Z" if attempt.finished_at else None, "outcome": attempt.status, "correct": attempt.correct, "speech_detected": attempt.speech_detected, "scores": attempt.scores, "selection": {"level": catalog["level"], "sublevel": catalog.get("sublevel"), "section": catalog["section"], "exercise": catalog["exerciseNumber"], "line": catalog["line"]}}


def child_progress(db: Session, parent: Parent, child_id: str, limit: int = 100) -> dict:
    owned_child(db, parent, child_id, active=False)
    filters = [PracticeSession.parent_id == parent.id, PracticeSession.child_id == child_id]
    counts = dict(db.execute(select(Attempt.status, func.count()).join(PracticeSession).where(*filters).group_by(Attempt.status)).all())
    total = sum(counts.values())
    attempts = db.scalars(select(Attempt).join(PracticeSession).where(*filters).order_by(Attempt.created_at.desc()).limit(limit))
    return {"summary": {"attempts": total, "completed": counts.get("correct", 0) + counts.get("incorrect", 0), "correct": counts.get("correct", 0)}, "attempts": [attempt_view(a) for a in attempts], "has_more": total > limit}
