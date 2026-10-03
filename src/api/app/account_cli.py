"""Operator-only privacy tooling. No public approval or development-login endpoint.

Run `python -m app.account_cli --help` from src/api. Commands require a configured
database and the operator's AWS identity. Keep flags OFF until legal review.
"""
import argparse
import json
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database import get_engine
from app.models.accounts import ConsentRecord, DeletionJob, Parent, utcnow
from app.services.privacy_service import export_ledger, notify, process_deletions, process_notifications, purge_orphaned_cognito_accounts, replay_ledger, retention_sweep


def approve_consent(db, *, parent_id, notice_version, evidence_reference, operator_id, reviewed_signed_consent):
    if not reviewed_signed_consent or notice_version != settings.privacy_notice_version:
        raise ValueError("Reviewed signed consent and current notice version are required")
    # Reference an access-controlled record; never include a signed document,
    # identity scan, name, address, or a public/document-download URL here.
    if not re.fullmatch(r"[A-Za-z0-9_:/.-]{3,200}", evidence_reference) or evidence_reference.startswith(("http:", "https:")):
        raise ValueError("Use a restricted evidence reference, not personal data or a download URL")
    if not re.fullmatch(r"[A-Za-z0-9_@.-]{3,128}", operator_id):
        raise ValueError("Operator identifier is invalid")
    UUID(parent_id)
    parent = db.scalar(select(Parent).where(Parent.id == parent_id).with_for_update())
    if not parent or parent.status != "active":
        raise ValueError("Parent is unavailable")
    if db.scalar(select(DeletionJob.id).where(DeletionJob.parent_id == parent_id, DeletionJob.status == "pending")):
        raise ValueError("Complete pending deletion first")
    consent = db.scalar(select(ConsentRecord).where(ConsentRecord.parent_id == parent_id).order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc()).limit(1))
    if not consent or consent.status != "pending" or consent.notice_version != notice_version:
        raise ValueError("No pending request exists for this privacy notice")
    consent.status, consent.method = "verified", "reviewed-signed-parental-consent"
    consent.evidence_reference, consent.verified_by, consent.verified_at = evidence_reference, operator_id, utcnow()
    parent.first_consent_verified_at = parent.first_consent_verified_at or utcnow()
    notify(db, "consent_review_completed", consent.id, parent.email)
    db.commit()
    return {"status": "verified", "child_collection_enabled": settings.child_data_collection_enabled}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("worker", help="Retry pending deletions and privacy email independently; run every minute")
    commands.add_parser("retention", help="Expire records and queue inactive account deletion; run daily")
    consent = commands.add_parser("approve-consent", help="Only after trained review of legally approved signed parental consent")
    for field in ("parent-id", "notice-version", "evidence-reference", "operator-id"):
        consent.add_argument("--" + field, required=True)
    consent.add_argument("--reviewed-signed-consent", action="store_true", required=True)
    commands.add_parser("status", help="Print queue counts without student or parent personal data")
    inspect_request = commands.add_parser("inspect-request", help="Show minimum parent and consent details for a notified request, on an authorized operator terminal")
    inspect_request.add_argument("--reference", type=UUID, required=True)
    export = commands.add_parser("export-ledger", help="Write minimal deletion ledger to a restricted file outside database backups")
    export.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser("replay-ledger", help="Reapply trusted deletion ledger after a restore, with collection flags OFF")
    replay.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    with Session(get_engine(), expire_on_commit=False) as db:
        if args.command == "worker":
            output = {"deletions": process_deletions(db), "notifications": process_notifications(db)}
        elif args.command == "retention":
            output = retention_sweep(db)
            output["orphaned_cognito_accounts"] = purge_orphaned_cognito_accounts(db)
        elif args.command == "approve-consent":
            output = approve_consent(db, parent_id=args.parent_id, notice_version=args.notice_version, evidence_reference=args.evidence_reference, operator_id=args.operator_id, reviewed_signed_consent=args.reviewed_signed_consent)
        elif args.command == "inspect-request":
            consent = db.get(ConsentRecord, str(args.reference))
            if not consent:
                raise ValueError("Consent request is unavailable")
            parent = db.get(Parent, consent.parent_id)
            output = {"reference": consent.id, "parent_id": parent.id, "parent_email": parent.email, "notice_version": consent.notice_version, "status": consent.status, "requested_at": consent.created_at.isoformat() + "Z"}
        elif args.command == "export-ledger":
            # Refuse to overwrite a file silently. Operator controls file permissions.
            with args.output.open("x", encoding="utf-8") as target:
                json.dump(export_ledger(db), target, indent=2)
            output = {"exported": True}
        elif args.command == "replay-ledger":
            if settings.accounts_enabled or settings.child_data_collection_enabled:
                raise ValueError("Disable accounts and child collection before restoring")
            output = {"replayed": replay_ledger(db, json.loads(args.input.read_text(encoding="utf-8")))}
        else:
            from sqlalchemy import func
            from app.models.accounts import PrivacyNotification
            output = {"deletions_pending": db.scalar(select(func.count()).select_from(DeletionJob).where(DeletionJob.status == "pending")), "notifications_pending": db.scalar(select(func.count()).select_from(PrivacyNotification).where(PrivacyNotification.status == "pending"))}
        print(json.dumps(output))
        if args.command == "worker" and (output["deletions"]["failed"] or output["notifications"]["failed"]):
            raise SystemExit(1)
        if args.command == "retention" and output["orphaned_cognito_accounts"]["failed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
