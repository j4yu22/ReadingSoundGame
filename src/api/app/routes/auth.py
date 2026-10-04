from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from datetime import timedelta
from functools import lru_cache
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import OAUTH_COOKIE, SESSION_COOKIE, require_accounts, require_parent, token_hash
from app.database import get_db
from app.models.accounts import DeletionLedger, Parent, ParentSession, utcnow
from app.services.privacy_service import lock_cognito_subject

router = APIRouter(prefix="/api/auth", tags=["auth"])


def signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="parent-oauth-v1")


def issuer() -> str:
    return f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"


@lru_cache(maxsize=4)
def jwks_client(url: str):
    return jwt.PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=10)


def validate_id_token(token: str, nonce: str) -> dict:
    key = jwks_client(issuer() + "/.well-known/jwks.json").get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.cognito_client_id,
                        issuer=issuer(), options={"require": ["exp", "iat", "auth_time", "sub", "iss", "aud", "nonce", "token_use", "email", "email_verified"]})
    if claims["token_use"] != "id" or claims["email_verified"] is not True or not hmac.compare_digest(str(claims["nonce"]), nonce):
        raise ValueError("Invalid authentication claims")
    if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 128 or not isinstance(claims["email"], str) or len(claims["email"]) > 320:
        raise ValueError("Invalid account identity")
    if not isinstance(claims["auth_time"], (int, float)) or not 0 <= time.time() - claims["auth_time"] <= 300:
        raise ValueError("A fresh interactive sign-in is required")
    return claims


def cookie(response: Response, name: str, value: str, seconds: int) -> None:
    response.set_cookie(name, value, max_age=seconds, httponly=True, secure=settings.secure_cookies, samesite="lax", path="/")


def _issue_session_for_verified_claims(db: Session, claims: dict) -> str:
    """Internal callback step; caller must first validate the ID token and nonce."""
    lock_cognito_subject(db, claims["sub"])
    erased = db.scalar(select(DeletionLedger.id).where(DeletionLedger.subject_hash == token_hash(claims["sub"]), DeletionLedger.expires_at > utcnow()))
    if erased:
        raise HTTPException(403, "This identity has been deleted.")
    parent = db.scalar(select(Parent).where(Parent.cognito_sub == claims["sub"]).with_for_update())
    if parent is None:
        if not settings.account_registration_open:
            raise HTTPException(403, "New parent registration is not open yet.")
        parent = Parent(cognito_sub=claims["sub"], cognito_username=claims.get("cognito:username", claims["sub"]), email=claims["email"])
        db.add(parent)
        db.flush()
    if parent.status != "active":
        raise HTTPException(403, "This account is pending deletion.")
    parent.email = claims["email"]
    parent.last_active_at = utcnow()
    raw = secrets.token_urlsafe(48)
    db.add(ParentSession(parent_id=parent.id, token_hash=token_hash(raw), expires_at=utcnow() + timedelta(hours=settings.session_hours)))
    db.commit()
    return raw


@router.get("/login")
def login() -> Response:
    require_accounts()
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urlencode({"client_id": settings.cognito_client_id, "response_type": "code", "scope": "openid email", "prompt": "login", "redirect_uri": settings.public_origin + "/api/auth/callback", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"})
    response = RedirectResponse(settings.cognito_domain + "/oauth2/authorize?" + query, status_code=303)
    cookie(response, OAUTH_COOKIE, signer().dumps({"state": state, "nonce": nonce, "verifier": verifier}), 600)
    return response


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)) -> Response:
    require_accounts()
    try:
        flow = signer().loads(request.cookies.get(OAUTH_COOKIE, ""), max_age=600)
        if not state or not code or not hmac.compare_digest(state, flow["state"]):
            raise ValueError("Invalid OAuth state")
        form = {"grant_type": "authorization_code", "client_id": settings.cognito_client_id, "redirect_uri": settings.public_origin + "/api/auth/callback", "code": code, "code_verifier": flow["verifier"]}
        auth = httpx.BasicAuth(settings.cognito_client_id, settings.cognito_client_secret) if settings.cognito_client_secret else None
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(settings.cognito_domain + "/oauth2/token", data=form, auth=auth)
            response.raise_for_status()
            claims = await asyncio.to_thread(validate_id_token, response.json()["id_token"], flow["nonce"])
    except (BadSignature, ValueError, KeyError, jwt.PyJWTError, httpx.HTTPError) as exc:
        raise HTTPException(401, "Sign-in could not be verified. Please start again.") from None
    raw = _issue_session_for_verified_claims(db, claims)
    result = RedirectResponse("/", status_code=303)
    cookie(result, SESSION_COOKIE, raw, settings.session_hours * 3600)
    result.delete_cookie(OAUTH_COOKIE, path="/", secure=settings.secure_cookies, httponly=True, samesite="lax")
    result.headers["Cache-Control"] = "no-store"
    return result


@router.post("/logout")
def logout(request: Request, parent: Parent = Depends(require_parent), db: Session = Depends(get_db)) -> Response:
    db.execute(delete(ParentSession).where(ParentSession.token_hash == token_hash(request.cookies[SESSION_COOKIE])))
    db.commit()
    from fastapi.responses import JSONResponse
    response = JSONResponse({"logout_url": settings.cognito_domain + "/logout?" + urlencode({"client_id": settings.cognito_client_id, "logout_uri": settings.public_origin + "/"})})
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.secure_cookies, httponly=True, samesite="lax")
    return response
