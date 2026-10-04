from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    # Database timestamps are always UTC, including SQLite test fixtures.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid4())


class Parent(Base):
    __tablename__ = "parents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cognito_sub: Mapped[str] = mapped_column(String(128), unique=True)
    cognito_username: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    first_consent_verified_at: Mapped[datetime | None] = mapped_column(DateTime)


class ParentSession(Base):
    __tablename__ = "parent_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"), index=True)
    notice_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    method: Mapped[str | None] = mapped_column(String(80))
    evidence_reference: Mapped[str | None] = mapped_column(String(200))
    verified_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime)


class Child(Base):
    __tablename__ = "children"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"), index=True)
    nickname: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ActivityVersion(Base):
    __tablename__ = "activity_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    catalog_id: Mapped[str] = mapped_column(String(120))
    snapshot: Mapped[dict] = mapped_column(JSON)
    scoring_version: Mapped[str] = mapped_column(String(40), default="azure-final-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PracticeSession(Base):
    __tablename__ = "practice_sessions"
    __table_args__ = (UniqueConstraint("parent_id", "request_id", name="uq_practice_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"), index=True)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("session_id", "request_id", name="uq_attempt_request"), CheckConstraint("status IN ('started','correct','incorrect','no_speech','technical_error','abandoned','microphone_unavailable')", name="ck_attempt_status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("practice_sessions.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    activity_version_id: Mapped[str] = mapped_column(ForeignKey("activity_versions.id"))
    status: Mapped[str] = mapped_column(String(32), default="started")
    speech_detected: Mapped[bool | None] = mapped_column(Boolean)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    scores: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    activity_version: Mapped[ActivityVersion] = relationship(lazy="joined")

    @property
    def activity_snapshot(self) -> dict:
        return self.activity_version.snapshot


class DeletionJob(Base):
    __tablename__ = "deletion_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # No foreign keys: jobs survive deletion of their target.
    parent_id: Mapped[str] = mapped_column(String(36), index=True)
    child_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    data_purged_at: Mapped[datetime | None] = mapped_column(DateTime)


class SpeechCheck(Base):
    __tablename__ = "speech_checks"
    __table_args__ = (UniqueConstraint("attempt_id", "mode", name="uq_speech_check"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(16), default="processing")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PrivacyNotification(Base):
    __tablename__ = "privacy_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event: Mapped[str] = mapped_column(String(40))
    reference: Mapped[str] = mapped_column(String(36))
    parent_email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(80))


class DeletionLedger(Base):
    __tablename__ = "deletion_ledger"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_id: Mapped[str] = mapped_column(String(36))
    child_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(24))
    subject_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
