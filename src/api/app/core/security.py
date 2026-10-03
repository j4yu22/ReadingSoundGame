from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database import get_db
from app.models.accounts import ConsentRecord, Parent, ParentSession, utcnow

SESSION_COOKIE = "rsg_session"
OAUTH_COOKIE = "rsg_oauth"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_accounts() -> None:
    if not settings.accounts_ready:
        raise HTTPException(503, "Accounts are not available. No child data can be collected.")


def csrf_token(session_token: str) -> str:
    return hmac.new(settings.session_secret.encode(), ("csrf:" + session_token).encode(), hashlib.sha256).hexdigest()


def require_csrf(request: Request, parent: Parent | None = None) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("origin") != settings.public_origin:
        raise HTTPException(403, "Request origin is not allowed.")
    token = request.cookies.get(SESSION_COOKIE, "")
    supplied = request.headers.get("x-csrf-token", "")
    if not token or not supplied or not hmac.compare_digest(supplied, csrf_token(token)):
        raise HTTPException(403, "Refresh the page before trying again.")


def require_parent(request: Request, db: Session = Depends(get_db)) -> Parent:
    require_accounts()
    raw = request.cookies.get(SESSION_COOKIE, "")
    if len(raw) < 32 or len(raw) > 256:
        raise HTTPException(401, "Parent sign-in is required.")
    login = db.get(ParentSession, token_hash(raw), populate_existing=True)
    if not login or login.expires_at <= utcnow():
        raise HTTPException(401, "Please sign in again.")
    parent = db.get(Parent, login.parent_id, populate_existing=True)
    if not parent or parent.status != "active":
        raise HTTPException(401, "This account is unavailable or pending deletion.")
    request.state.parent_session = login
    require_csrf(request, parent)
    return parent


def require_recent_parent(request: Request, parent: Parent = Depends(require_parent)) -> Parent:
    if request.state.parent_session.created_at < utcnow() - timedelta(minutes=15):
        raise HTTPException(403, "Please sign in again to manage privacy settings.")
    return parent


def consent_status(db: Session, parent: Parent) -> str:
    latest = db.scalar(select(ConsentRecord).where(ConsentRecord.parent_id == parent.id).order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc()).limit(1))
    if not latest:
        return "none"
    if latest.notice_version != settings.privacy_notice_version:
        return "notice_changed"
    return latest.status


def assert_consented(db: Session, parent: Parent, *, lock: bool = False) -> Parent:
    require_accounts()
    query = select(Parent).where(Parent.id == parent.id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    current = db.scalar(query)
    if not current or current.status != "active":
        raise HTTPException(403, "This account is unavailable.")
    if not settings.child_data_collection_enabled:
        raise HTTPException(403, "Child practice is not open yet.")
    if consent_status(db, current) != "verified":
        raise HTTPException(403, "Verified parental consent is required before child practice.")
    return current


def require_consented_parent(request: Request, db: Session = Depends(get_db), parent: Parent = Depends(require_parent)) -> Parent:
    return assert_consented(db, parent)


def get_practice_db():
    """Public practice must not even create an account database connection."""
    if not settings.accounts_enabled:
        yield None
        return
    require_accounts()
    yield from get_db()


def require_practice_parent(request: Request, db: Session | None = Depends(get_practice_db)) -> Parent | None:
    if not settings.accounts_enabled:
        return None
    # Reuse the exact session, CSRF, live status, and consent checks used by
    # account endpoints. Missing configuration cannot turn this into guest use.
    parent = require_parent(request, db)
    return require_consented_parent(request, db, parent)
