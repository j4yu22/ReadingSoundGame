"""Deletion and retention are independent of notification delivery.

Worker errors retain only an exception class, never provider responses or personal data.
Export the bounded deletion ledger outside database backups before restoring old data.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import token_hash
from app.models.accounts import Child, ConsentRecord, DeletionJob, DeletionLedger, Parent, ParentSession, PracticeSession, PrivacyNotification, new_id, utcnow
from app.services.account_service import owned_child


def notify(db: Session, event: str, reference: str, parent_email: str | None) -> None:
    db.add(PrivacyNotification(event=event, reference=reference, parent_email=parent_email))


def lock_cognito_subject(db: Session, subject: str) -> None:
    """Serialize first sign-in and orphan erasure even before a Parent row exists.

    The lock lives until commit/rollback. SQLite is only a serial unit-test path;
    the opt-in PostgreSQL suite exercises the actual concurrent behavior.
    """
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        digest = bytes.fromhex(token_hash(f"rsg:cognito:{settings.cognito_user_pool_id}:{subject}"))
        key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:identity_lock)"), {"identity_lock": key})
    elif dialect != "sqlite":
        raise RuntimeError("Cognito identity coordination requires PostgreSQL")


def scrub_parent_notification_contacts(db: Session, parent_id: str, email: str | None = None) -> None:
    """Remove outbox contact before consent/parent rows lose their relationship.

    Outbox records deliberately survive their target, so database cascades cannot
    perform this step. Reference matching also covers a previously changed email;
    contact matching covers a notification whose older request has expired.
    """
    references = list(db.scalars(select(ConsentRecord.id).where(ConsentRecord.parent_id == parent_id)))
    references += list(db.scalars(select(DeletionJob.id).where(DeletionJob.parent_id == parent_id)))
    criteria = [PrivacyNotification.reference.in_(references)]
    if email:
        criteria.append(PrivacyNotification.parent_email == email)
    db.execute(update(PrivacyNotification).where(or_(*criteria)).values(parent_email=None))


def queue_deletion(db: Session, parent: Parent, kind: str, child_id: str | None = None) -> DeletionJob:
    parent = db.scalar(select(Parent).where(Parent.id == parent.id).with_for_update().execution_options(populate_existing=True))
    if not parent or parent.status != "active":
        raise HTTPException(403, "Account is unavailable.")
    existing = db.scalar(select(DeletionJob).where(DeletionJob.parent_id == parent.id, DeletionJob.kind == kind, DeletionJob.child_id == child_id, DeletionJob.status == "pending"))
    if existing:
        return existing
    if kind == "child":
        child = owned_child(db, parent, child_id)
        child.status = "deletion_pending"
    elif kind in {"parent", "withdrawal"}:
        if kind == "parent":
            parent.status = "deletion_pending"
        for child in db.scalars(select(Child).where(Child.parent_id == parent.id)):
            child.status = "deletion_pending"
        for consent in db.scalars(select(ConsentRecord).where(ConsentRecord.parent_id == parent.id)):
            consent.status, consent.withdrawn_at = "withdrawn", utcnow()
        db.execute(delete(ParentSession).where(ParentSession.parent_id == parent.id))
    else:
        raise ValueError("Unsupported deletion type")
    job = DeletionJob(parent_id=parent.id, child_id=child_id, kind=kind)
    db.add(job)
    db.flush()
    db.add(DeletionLedger(parent_id=parent.id, child_id=child_id, kind=kind, subject_hash=token_hash(parent.cognito_sub) if kind == "parent" else None, expires_at=utcnow() + timedelta(days=settings.deletion_ledger_days)))
    notify(db, "deletion_requested", job.id, parent.email)
    db.commit()
    return job


def erase_target(db: Session, parent_id: str, child_id: str | None, kind: str, cutoff: datetime | None = None) -> None:
    if kind == "parent":
        parent = db.get(Parent, parent_id)
        scrub_parent_notification_contacts(db, parent_id, parent.email if parent else None)
        db.execute(delete(Parent).where(Parent.id == parent_id))
    elif kind == "child":
        db.execute(delete(Child).where(Child.id == child_id, Child.parent_id == parent_id))
    elif kind == "withdrawal":
        criteria = [Child.parent_id == parent_id]
        if cutoff:
            criteria.append(Child.created_at <= cutoff)
        db.execute(delete(Child).where(*criteria))
        db.execute(delete(ParentSession).where(ParentSession.parent_id == parent_id))
        for consent in db.scalars(select(ConsentRecord).where(ConsentRecord.parent_id == parent_id, ConsentRecord.created_at <= (cutoff or utcnow()))):
            consent.status, consent.withdrawn_at = "withdrawn", utcnow()
            consent.evidence_reference, consent.verified_by = None, None


def process_deletions(db: Session, cognito=None, limit: int = 100) -> dict:
    import boto3
    ids = list(db.scalars(select(DeletionJob.id).where(DeletionJob.status == "pending").order_by(DeletionJob.attempts, DeletionJob.created_at).limit(limit)))
    completed, failed = 0, 0
    db.commit()
    for job_id in ids:
        job = db.scalar(select(DeletionJob).where(DeletionJob.id == job_id, DeletionJob.status == "pending").with_for_update(skip_locked=True))
        if not job:
            continue
        try:
            parent = db.scalar(select(Parent).where(Parent.id == job.parent_id).with_for_update())
            email = parent.email if parent else None
            if job.kind == "parent" and parent:
                # Purge child records even when Cognito is unavailable. Persist the
                # purge before calling AWS; retain only the identity mapping needed
                # to retry deleting the disabled Cognito account.
                if job.data_purged_at is None:
                    scrub_parent_notification_contacts(db, parent.id, parent.email)
                    db.execute(delete(Child).where(Child.parent_id == parent.id))
                    db.execute(delete(ConsentRecord).where(ConsentRecord.parent_id == parent.id))
                    db.execute(delete(ParentSession).where(ParentSession.parent_id == parent.id))
                    parent.email = ""
                    job.data_purged_at = utcnow()
                    db.commit()
                    job = db.scalar(select(DeletionJob).where(DeletionJob.id == job_id, DeletionJob.status == "pending").with_for_update(skip_locked=True))
                    if not job:
                        continue
                    parent = db.scalar(select(Parent).where(Parent.id == job.parent_id).with_for_update())
                    email = None
                client = cognito or boto3.client("cognito-idp", region_name=settings.cognito_region)
                if parent:
                    try:
                        client.admin_delete_user(UserPoolId=settings.cognito_user_pool_id, Username=parent.cognito_username)
                    except client.exceptions.UserNotFoundException:
                        pass
            erase_target(db, job.parent_id, job.child_id, job.kind, job.created_at)
            job.attempts += 1
            job.status, job.completed_at, job.last_error = "complete", utcnow(), None
            job.data_purged_at = job.data_purged_at or utcnow()
            notify(db, "deletion_completed", job.id, email)
            db.commit()
            completed += 1
        except Exception as exc:
            db.rollback()
            retry = db.get(DeletionJob, job_id)
            if retry:
                retry.attempts += 1
                retry.last_error = type(exc).__name__[:80]
                db.commit()
            failed += 1
    return {"completed": completed, "failed": failed}


def process_notifications(db: Session, ses=None, limit: int = 100) -> dict:
    import boto3
    recipients = [email.strip() for email in settings.privacy_notification_emails.split(",") if email.strip()]
    if not settings.privacy_notification_from or not recipients:
        return {"sent": 0, "failed": 0, "configured": False}
    ids = list(db.scalars(select(PrivacyNotification.id).where(PrivacyNotification.status == "pending").order_by(PrivacyNotification.attempts, PrivacyNotification.created_at).limit(limit)))
    db.commit()
    sent, failed = 0, 0
    for notification_id in ids:
        item = db.scalar(select(PrivacyNotification).where(PrivacyNotification.id == notification_id, PrivacyNotification.status == "pending").with_for_update(skip_locked=True))
        if not item:
            continue
        try:
            client = ses or boto3.client("ses", region_name=settings.cognito_region)
            message = f"Privacy event: {item.event}\nReference: {item.reference}\nParent contact: {item.parent_email or 'removed'}\n\nUse the authorized operator workflow to review this request. No child records are included in this email."
            client.send_email(Source=settings.privacy_notification_from, Destination={"ToAddresses": recipients}, Message={"Subject": {"Data": f"Reading Sound Games: {item.event}"}, "Body": {"Text": {"Data": message}}})
            item.status, item.sent_at, item.last_error, item.parent_email = "sent", utcnow(), None, None
            item.attempts += 1
            db.commit()
            sent += 1
        except Exception as exc:
            db.rollback()
            item = db.get(PrivacyNotification, notification_id)
            item.attempts += 1
            item.last_error = type(exc).__name__[:80]
            db.commit()
            failed += 1
    return {"sent": sent, "failed": failed, "configured": True}


def retention_sweep(db: Session) -> dict:
    now = utcnow()
    counts = {}
    # Sessions cascade attempts and speech claims. No scores survive their parent session.
    counts["practice_sessions"] = db.execute(delete(PracticeSession).where(PracticeSession.created_at < now - timedelta(days=settings.retention_days))).rowcount
    counts["login_sessions"] = db.execute(delete(ParentSession).where(ParentSession.expires_at <= now)).rowcount
    counts["ledger"] = db.execute(delete(DeletionLedger).where(DeletionLedger.expires_at <= now)).rowcount
    counts["completed_jobs"] = db.execute(delete(DeletionJob).where(DeletionJob.status == "complete", DeletionJob.completed_at < now - timedelta(days=settings.deletion_ledger_days))).rowcount
    # Pending requests lose contact after seven days but remain retryable by reference.
    for item in db.scalars(select(PrivacyNotification).where(PrivacyNotification.created_at < now - timedelta(days=7))):
        item.parent_email = None
    counts["notifications"] = db.execute(delete(PrivacyNotification).where(PrivacyNotification.status == "sent", PrivacyNotification.sent_at < now - timedelta(days=30))).rowcount
    counts["old_consent"] = db.execute(delete(ConsentRecord).where(ConsentRecord.status.in_(["withdrawn", "superseded"]), ConsentRecord.created_at < now - timedelta(days=settings.deletion_ledger_days))).rowcount
    db.commit()
    # Pending-only registrations expire after 30 days; other inactive accounts
    # expire after one year. Parent deletion also removes the Cognito identity.
    queued = 0
    for parent in list(db.scalars(select(Parent).where(Parent.status == "active", Parent.created_at < now - timedelta(days=30)))):
        if parent.first_consent_verified_at is None or parent.last_active_at < now - timedelta(days=365):
            queue_deletion(db, parent, "parent")
            queued += 1
    counts["expired_accounts_queued"] = queued
    return counts


def export_ledger(db: Session) -> list[dict]:
    return [{"id": row.id, "parent_id": row.parent_id, "child_id": row.child_id, "kind": row.kind, "subject_hash": row.subject_hash, "created_at": row.created_at.isoformat(), "expires_at": row.expires_at.isoformat()} for row in db.scalars(select(DeletionLedger).where(DeletionLedger.expires_at > utcnow()))]


def replay_ledger(db: Session, entries: list[dict]) -> int:
    # Keep site collection OFF throughout restore. Source must be a trusted operator export.
    from uuid import UUID
    for entry in entries:
        for field in ("id", "parent_id"):
            UUID(entry[field])
        if entry.get("child_id"):
            UUID(entry["child_id"])
        if entry["kind"] not in {"parent", "child", "withdrawal"}:
            raise ValueError("Invalid ledger type")
    # Never resurrect sessions from a snapshot, including parents without a
    # deletion request. They may have explicitly signed out after the backup.
    db.execute(delete(ParentSession))
    for entry in entries:
        created = datetime.fromisoformat(entry["created_at"])
        expiry = datetime.fromisoformat(entry["expires_at"])
        if expiry <= utcnow():
            raise ValueError("Expired deletion ledger: backup may be too old to restore safely")
        erase_target(db, entry["parent_id"], entry.get("child_id"), entry["kind"], created)
        if not db.get(DeletionLedger, entry["id"]):
            db.add(DeletionLedger(id=entry["id"], parent_id=entry["parent_id"], child_id=entry.get("child_id"), kind=entry["kind"], subject_hash=entry.get("subject_hash"), created_at=created, expires_at=expiry))
    db.commit()
    return len(entries)


def purge_orphaned_cognito_accounts(db: Session, cognito=None) -> dict:
    """Expire signups that never completed the app callback (including unconfirmed users)."""
    import boto3
    from datetime import timezone
    if not settings.cognito_user_pool_id:
        return {"deleted": 0, "failed": 0, "configured": False}
    client = cognito or boto3.client("cognito-idp", region_name=settings.cognito_region)
    deleted, failed = 0, 0
    cutoff = utcnow() - timedelta(days=30)
    try:
        for page in client.get_paginator("list_users").paginate(UserPoolId=settings.cognito_user_pool_id):
            for user in page.get("Users", []):
                created = user.get("UserCreateDate")
                if not created:
                    continue
                created = created.astimezone(timezone.utc).replace(tzinfo=None) if created.tzinfo else created
                attributes = {a["Name"]: a["Value"] for a in user.get("Attributes", [])}
                subject = attributes.get("sub")
                if created >= cutoff or not subject:
                    continue
                try:
                    lock_cognito_subject(db, subject)
                    if db.scalar(select(Parent.id).where(Parent.cognito_sub == subject)):
                        db.commit()
                        continue
                    # Persist an erasure intent before the remote call. A callback
                    # may already hold a valid ID token for this old signup; after
                    # the lock is released it must see this marker and refuse to
                    # recreate the identity, even if Cognito deletion times out.
                    subject_hash = token_hash(subject)
                    marker = db.scalar(select(DeletionLedger.id).where(DeletionLedger.subject_hash == subject_hash, DeletionLedger.expires_at > utcnow()))
                    if not marker:
                        db.add(DeletionLedger(parent_id=new_id(), kind="parent", subject_hash=subject_hash, expires_at=utcnow() + timedelta(days=settings.deletion_ledger_days)))
                    db.commit()
                    client.admin_delete_user(UserPoolId=settings.cognito_user_pool_id, Username=user["Username"])
                    deleted += 1
                except client.exceptions.UserNotFoundException:
                    pass
                except Exception:
                    db.rollback()
                    failed += 1
    except Exception:
        db.rollback()
        failed += 1
    return {"deleted": deleted, "failed": failed, "configured": True}
