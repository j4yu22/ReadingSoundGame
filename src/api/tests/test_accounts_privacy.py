from __future__ import annotations

import json
import secrets
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import account_cli
from app.core import config, security
from app.database import Base, get_db
from app.models.accounts import ActivityVersion, Attempt, Child, ConsentRecord, DeletionJob, DeletionLedger, Parent, ParentSession, PracticeSession, PrivacyNotification, SpeechCheck, utcnow
from app.routes import accounts, auth, practice, speech
from app.services import privacy_service
from app.services.account_service import begin_speech_check, create_attempt, create_practice, get_owned_attempt, record_speech_error, record_speech_result


class AccountPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.settings = replace(config.settings, accounts_enabled=True, account_registration_open=True, child_data_collection_enabled=True, database_url="postgresql+psycopg://test@localhost/test", session_secret="test-session-secret-not-for-deployment-123456789", public_origin="https://practice.example", cognito_domain="https://test.auth.us-west-2.amazoncognito.com", cognito_user_pool_id="us-west-2_test", cognito_client_id="test-client", cognito_client_secret="test-client-secret", privacy_contact="privacy@example.test", privacy_notice_version="reviewed-test-notice", privacy_notification_from="privacy@example.test")
        for module in (security, auth, accounts, speech, privacy_service, account_cli):
            self.stack.enter_context(patch.object(module, "settings", self.settings))
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.db.close)
        self.parent = Parent(cognito_sub="parent-a", cognito_username="parent-a", email="parent-a@example.test")
        self.other = Parent(cognito_sub="parent-b", cognito_username="parent-b", email="parent-b@example.test")
        self.db.add_all([self.parent, self.other])
        self.db.flush()
        self.child = Child(parent_id=self.parent.id, nickname="Fox")
        self.foreign_child = Child(parent_id=self.other.id, nickname="Owl")
        self.db.add_all([self.child, self.foreign_child, ConsentRecord(parent_id=self.parent.id, notice_version=self.settings.privacy_notice_version, status="verified"), ConsentRecord(parent_id=self.other.id, notice_version=self.settings.privacy_notice_version, status="verified")])
        self.token = secrets.token_urlsafe(48)
        self.login = ParentSession(parent_id=self.parent.id, token_hash=security.token_hash(self.token), expires_at=utcnow() + timedelta(hours=8))
        self.db.add(self.login)
        self.db.commit()
        app = FastAPI()
        for router in (accounts.router, auth.router, practice.router, speech.router):
            app.include_router(router)
        def session_override():
            with Session(self.engine, expire_on_commit=False) as db:
                yield db
        app.dependency_overrides[get_db] = session_override
        app.dependency_overrides[security.get_practice_db] = session_override
        self.client = self.stack.enter_context(TestClient(app, base_url=self.settings.public_origin))
        self.client.cookies.set(security.SESSION_COOKIE, self.token)
        self.headers = {"Origin": self.settings.public_origin, "X-CSRF-Token": security.csrf_token(self.token)}

    def attempt(self):
        session = create_practice(self.db, self.parent, self.child.id, str(uuid4()))
        return create_attempt(self.db, self.parent, session.id, str(uuid4()), {"level": "D", "sublevel": 1, "section": "standard", "exercise": "1", "line": "a"})

    def test_defaults_are_closed(self):
        self.assertFalse(config.Settings().accounts_enabled)
        self.assertFalse(config.Settings().account_registration_open)
        self.assertFalse(config.Settings().child_data_collection_enabled)
        self.assertFalse(replace(self.settings, session_secret="short").accounts_ready)
        self.assertFalse(replace(self.settings, cognito_client_secret="").accounts_ready)
        self.assertFalse(replace(self.settings, public_origin="http://example.com").accounts_ready)
        self.assertFalse(replace(self.settings, database_url="sqlite://").accounts_ready)
        self.assertFalse(replace(self.settings, deletion_ledger_days=10).accounts_ready)

    def test_cookie_hash_and_csrf_origin_are_required(self):
        self.assertNotEqual(self.login.token_hash, self.token)
        me = self.client.get("/api/account/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["csrf_token"], self.headers["X-CSRF-Token"])
        for headers in ({}, {"Origin": "https://attacker.test", "X-CSRF-Token": self.headers["X-CSRF-Token"]}, {"Origin": self.settings.public_origin, "X-CSRF-Token": "bad"}):
            self.assertEqual(self.client.post("/api/account/children", json={"nickname": "Mouse"}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/account/children", json={"nickname": "Mouse"}, headers=self.headers).status_code, 201)

    def test_parent_ownership_never_exposes_other_child(self):
        self.assertEqual(self.client.get(f"/api/account/children/{self.foreign_child.id}/progress").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/account/children/{self.foreign_child.id}", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post("/api/practice/sessions", json={"child_id": self.foreign_child.id, "request_id": str(uuid4())}, headers=self.headers).status_code, 404)
        self.assertNotIn(self.foreign_child.id, self.client.get("/api/account/export").text)

    def test_pending_consent_never_enables_collection(self):
        consent = self.db.scalar(select(ConsentRecord).where(ConsentRecord.parent_id == self.parent.id))
        consent.status = "withdrawn"
        self.db.commit()
        response = self.client.post("/api/account/consent", json={"notice_version": self.settings.privacy_notice_version, "adult_confirmed": True}, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(self.client.post("/api/account/children", json={"nickname": "Mouse"}, headers=self.headers).status_code, 403)
        self.assertEqual(self.client.post("/api/account/consent", json={"notice_version": self.settings.privacy_notice_version, "adult_confirmed": True, "status": "verified"}, headers=self.headers).status_code, 422)
        with self.assertRaises(ValueError):
            account_cli.approve_consent(self.db, parent_id=self.parent.id, notice_version=self.settings.privacy_notice_version, evidence_reference="ref-123", operator_id="reviewer", reviewed_signed_consent=False)
        account_cli.approve_consent(self.db, parent_id=self.parent.id, notice_version=self.settings.privacy_notice_version, evidence_reference="restricted:ref-123", operator_id="reviewer", reviewed_signed_consent=True)
        self.assertEqual(self.client.get("/api/account/me").json()["consent_status"], "verified")
        with patch.object(security, "settings", replace(self.settings, child_data_collection_enabled=False)):
            self.assertEqual(self.client.post("/api/account/children", json={"nickname": "Mouse"}, headers=self.headers).status_code, 403)

    def test_account_speech_never_falls_back_to_guest(self):
        from tests.test_privacy_speech_boundary import recording
        headers = {**self.headers, "Content-Type": "audio/wav"}
        path = "/api/speech/listen-check?mode=final&expected=browser-answer"
        with patch.object(speech, "recognize_wav_bytes") as recognize:
            # A verified account still needs its own persisted attempt.
            self.assertEqual(self.client.post(path, content=recording(), headers=headers).status_code, 422)
            self.assertEqual(self.client.post(path, content=recording(), headers={"Content-Type": "audio/wav"}).status_code, 403)
            self.assertEqual(self.client.post(path + "&attempt_id=not-owned", content=recording(), headers=headers).status_code, 404)
            with patch.object(security, "settings", replace(self.settings, child_data_collection_enabled=False)):
                self.assertEqual(self.client.post(path, content=recording(), headers=headers).status_code, 403)
            consent = self.db.scalar(select(ConsentRecord).where(ConsentRecord.parent_id == self.parent.id))
            consent.status = "withdrawn"
            self.db.commit()
            self.assertEqual(self.client.post(path, content=recording(), headers=headers).status_code, 403)
            self.client.cookies.clear()
            self.assertEqual(self.client.post(path, content=recording(), headers=headers).status_code, 401)
            recognize.assert_not_called()

    def test_stale_notice_and_session_fail_closed(self):
        with patch.object(security, "settings", replace(self.settings, privacy_notice_version="next-notice")):
            self.assertEqual(self.client.post("/api/account/children", json={"nickname": "Mouse"}, headers=self.headers).status_code, 403)
        self.login.created_at = utcnow() - timedelta(minutes=16)
        self.db.commit()
        self.assertEqual(self.client.delete("/api/account", headers=self.headers).status_code, 403)
        self.login.expires_at = utcnow() - timedelta(seconds=1)
        self.db.commit()
        self.assertEqual(self.client.get("/api/account/me").status_code, 401)

    def test_idempotency_does_not_change_child_or_activity(self):
        request_id = str(uuid4())
        session = create_practice(self.db, self.parent, self.child.id, request_id)
        self.assertEqual(session.id, create_practice(self.db, self.parent, self.child.id, request_id).id)
        extra = Child(parent_id=self.parent.id, nickname="Ant")
        self.db.add(extra)
        self.db.commit()
        with self.assertRaises(HTTPException):
            create_practice(self.db, self.parent, extra.id, request_id)
        selection = {"level": "D", "sublevel": 1, "section": "standard", "exercise": "1", "line": "a"}
        attempt_id = str(uuid4())
        attempt = create_attempt(self.db, self.parent, session.id, attempt_id, selection)
        self.assertEqual(attempt.id, create_attempt(self.db, self.parent, session.id, attempt_id, selection).id)
        with self.assertRaises(HTTPException):
            create_attempt(self.db, self.parent, session.id, attempt_id, {**selection, "line": "b"})
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Attempt)), 1)

    def test_presence_is_not_accuracy_and_no_transcripts_saved(self):
        attempt = self.attempt()
        begin_speech_check(self.db, self.parent, attempt.id, "presence")
        result = {"speechDetected": True, "correct": True, "recognizedText": "private child speech", "words": ["private"], "scores": {"accuracy": 99}}
        record_speech_result(self.db, self.parent, attempt.id, "presence", result)
        self.assertIsNone(attempt.correct)
        self.assertIsNone(attempt.scores)
        self.assertEqual(attempt.status, "started")
        with self.assertRaises(HTTPException):
            begin_speech_check(self.db, self.parent, attempt.id, "presence")
        begin_speech_check(self.db, self.parent, attempt.id, "final")
        record_speech_result(self.db, self.parent, attempt.id, "final", {**result, "correct": False, "scores": {"accuracy": 45, "fluency": float("nan"), "private": 10}})
        self.assertEqual(attempt.status, "incorrect")
        self.assertEqual(attempt.scores, {"accuracy": 45.0})
        exported = self.client.get("/api/account/export").text
        self.assertNotIn("private child speech", exported)
        self.assertNotIn("recognizedText", exported)
        self.assertEqual(self.client.get(f"/api/account/children/{self.child.id}/progress").json()["summary"], {"attempts": 1, "completed": 1, "correct": 0})

    def test_no_speech_and_technical_failure_are_not_incorrect(self):
        for outcome in ("no_speech", "technical_error"):
            attempt = self.attempt()
            begin_speech_check(self.db, self.parent, attempt.id, "presence")
            if outcome == "no_speech":
                record_speech_result(self.db, self.parent, attempt.id, "presence", {"speechDetected": False, "correct": False})
            else:
                record_speech_error(self.db, self.parent, attempt.id, "presence")
            self.assertEqual(attempt.status, outcome)
            self.assertIsNone(attempt.correct)

    def test_delete_child_blocks_inflight_result_then_cascades(self):
        attempt = self.attempt()
        begin_speech_check(self.db, self.parent, attempt.id, "presence")
        response = self.client.delete(f"/api/account/children/{self.child.id}", headers=self.headers)
        self.assertEqual(response.status_code, 202)
        with self.assertRaises(HTTPException):
            record_speech_result(self.db, self.parent, attempt.id, "presence", {"speechDetected": True})
        self.db.rollback()
        self.assertEqual(privacy_service.process_deletions(self.db), {"completed": 1, "failed": 0})
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Attempt)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SpeechCheck)), 0)
        self.assertIsNotNone(self.db.get(Child, self.foreign_child.id))
        self.assertIsNotNone(self.db.get(Parent, self.parent.id))

    def test_withdrawal_revokes_sessions_and_requires_reverification(self):
        self.attempt()
        response = self.client.post("/api/account/consent/withdraw", headers=self.headers)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.get("/api/account/me").status_code, 401)
        self.assertEqual(privacy_service.process_deletions(self.db)["completed"], 1)
        self.db.expire_all()
        self.assertIsNone(self.db.get(Child, self.child.id))
        self.assertIsNotNone(self.db.get(Parent, self.parent.id))
        self.assertEqual(security.consent_status(self.db, self.parent), "withdrawn")

    def test_parent_deletion_retries_cognito_and_mail_independently(self):
        self.attempt()
        response = self.client.delete("/api/account", headers=self.headers)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.get("/api/account/me").status_code, 401)
        cognito = Mock()
        cognito.exceptions.UserNotFoundException = type("UserNotFound", (Exception,), {})
        cognito.admin_delete_user.side_effect = RuntimeError("provider failure with sensitive details")
        self.assertEqual(privacy_service.process_deletions(self.db, cognito)["failed"], 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Attempt)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Child).where(Child.parent_id == self.parent.id)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ConsentRecord).where(ConsentRecord.parent_id == self.parent.id)), 0)
        self.assertEqual(self.db.scalar(select(Parent.email).where(Parent.id == self.parent.id)), "")
        job = self.db.get(DeletionJob, response.json()["reference"])
        self.assertEqual(job.last_error, "RuntimeError")
        cognito.admin_delete_user.side_effect = None
        self.assertEqual(privacy_service.process_deletions(self.db, cognito)["completed"], 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Parent).where(Parent.id == self.parent.id)), 0)
        ses = Mock()
        ses.send_email.side_effect = RuntimeError("mail unavailable")
        self.assertGreater(privacy_service.process_notifications(self.db, ses)["failed"], 0)
        self.assertEqual(job.status, "complete")
        ses.send_email.side_effect = None
        self.assertGreater(privacy_service.process_notifications(self.db, ses)["sent"], 0)
        self.assertEqual(ses.send_email.call_args.kwargs["Destination"]["ToAddresses"], ["gina_underwood@yahoo.com", "jay.e.underwood@gmail.com"])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(PrivacyNotification).where(PrivacyNotification.parent_email.is_not(None))), 0)

    def test_retention_and_restore_replay_do_not_revive_child_records(self):
        attempt = self.attempt()
        session = self.db.get(PracticeSession, attempt.session_id)
        session.created_at = utcnow() - timedelta(days=self.settings.retention_days + 1)
        self.db.commit()
        self.assertEqual(privacy_service.retention_sweep(self.db)["practice_sessions"], 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Attempt)), 0)
        job = privacy_service.queue_deletion(self.db, self.parent, "child", self.child.id)
        ledger = privacy_service.export_ledger(self.db)
        privacy_service.process_deletions(self.db)
        self.db.expire_all()
        restored = Child(id=ledger[0]["child_id"], parent_id=self.parent.id, nickname="restored")
        self.db.add(restored)
        self.db.add(ParentSession(parent_id=self.other.id, token_hash=security.token_hash("unrelated-restored-session"), expires_at=utcnow() + timedelta(hours=1)))
        self.db.commit()
        privacy_service.replay_ledger(self.db, ledger)
        self.db.expire_all()
        self.assertIsNone(self.db.get(Child, restored.id))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DeletionLedger)), 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ParentSession)), 0)

    def test_parent_restore_replay_scrubs_linked_and_expired_request_contacts(self):
        parent_id, parent_email = self.parent.id, self.parent.email
        consent = self.db.scalar(select(ConsentRecord).where(ConsentRecord.parent_id == parent_id))
        self.db.add_all([
            PrivacyNotification(event="consent_review_requested", reference=consent.id, parent_email="previous-parent-email@example.test"),
            PrivacyNotification(event="consent_review_requested", reference=str(uuid4()), parent_email=parent_email),
            PrivacyNotification(event="consent_review_requested", reference=str(uuid4()), parent_email=self.other.email),
        ])
        self.db.commit()
        privacy_service.queue_deletion(self.db, self.parent, "parent")
        ledger = privacy_service.export_ledger(self.db)
        privacy_service.replay_ledger(self.db, ledger)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Parent).where(Parent.id == parent_id)), 0)
        contacts = list(self.db.scalars(select(PrivacyNotification.parent_email).where(PrivacyNotification.parent_email.is_not(None))))
        self.assertEqual(contacts, [self.other.email])
        # Replaying again must not affect an unrelated parent's notification.
        privacy_service.replay_ledger(self.db, ledger)
        self.assertEqual(list(self.db.scalars(select(PrivacyNotification.parent_email).where(PrivacyNotification.parent_email.is_not(None)))), contacts)

    def test_oauth_uses_pkce_and_cookie_only_state(self):
        response = self.client.get("/api/auth/login", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("code_challenge_method=S256", response.headers["location"])
        self.assertIn("prompt=login", response.headers["location"])
        self.assertNotIn("code_verifier", response.headers["location"])
        for flag in ("HttpOnly", "Secure", "SameSite=lax"):
            self.assertIn(flag, response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/auth/callback?code=bad&state=bad", follow_redirects=False).status_code, 401)
        response = self.client.post("/api/auth/logout", headers=self.headers)
        self.assertIn("/logout?", response.json()["logout_url"])
        self.assertEqual(self.client.get("/api/account/me").status_code, 401)

    def test_failed_remote_job_does_not_starve_new_database_only_delete(self):
        foreign_child_id = self.foreign_child.id
        privacy_service.queue_deletion(self.db, self.parent, "parent")
        privacy_service.queue_deletion(self.db, self.other, "child", self.foreign_child.id)
        cognito = Mock()
        cognito.exceptions.UserNotFoundException = type("UserNotFound", (Exception,), {})
        cognito.admin_delete_user.side_effect = RuntimeError("offline")
        self.assertEqual(privacy_service.process_deletions(self.db, cognito, limit=1), {"completed": 0, "failed": 1})
        self.assertEqual(privacy_service.process_deletions(self.db, cognito, limit=1), {"completed": 1, "failed": 0})
        self.db.expire_all()
        self.assertIsNone(self.db.get(Child, foreign_child_id))

    def test_jwt_signature_claims_and_fresh_auth_time_are_validated(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {"sub": "test-parent", "iss": auth.issuer(), "aud": self.settings.cognito_client_id, "iat": int(time.time()), "auth_time": int(time.time()), "exp": int(time.time()) + 300, "nonce": "test-nonce", "token_use": "id", "email": "test@example.test", "email_verified": True}
        jwks = Mock()
        jwks.get_signing_key_from_jwt.return_value.key = private_key.public_key()
        with patch.object(auth, "jwks_client", return_value=jwks):
            valid = jwt.encode(claims, private_key, algorithm="RS256")
            self.assertEqual(auth.validate_id_token(valid, "test-nonce")["sub"], "test-parent")
            cases = [{"aud": "other-client"}, {"iss": "https://attacker.test"}, {"exp": int(time.time()) - 1}, {"nonce": "other-nonce"}, {"email_verified": False}, {"token_use": "access"}, {"auth_time": int(time.time()) - 600}]
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises((jwt.PyJWTError, ValueError)):
                    auth.validate_id_token(jwt.encode({**claims, **changes}, private_key, algorithm="RS256"), "test-nonce")
            with self.assertRaises(jwt.PyJWTError):
                auth.validate_id_token(jwt.encode(claims, wrong_key, algorithm="RS256"), "test-nonce")

    def test_orphaned_cognito_cleanup_preserves_linked_and_recent_accounts(self):
        cognito = Mock()
        cognito.exceptions.UserNotFoundException = type("UserNotFound", (Exception,), {})
        old = utcnow() - timedelta(days=31)
        users = [{"Username": sub, "Attributes": [{"Name": "sub", "Value": sub}], "UserCreateDate": created} for sub, created in ((self.parent.cognito_sub, old), ("orphan-old", old), ("orphan-new", utcnow()))]
        cognito.get_paginator.return_value.paginate.return_value = [{"Users": users}]
        self.assertEqual(privacy_service.purge_orphaned_cognito_accounts(self.db, cognito)["deleted"], 1)
        cognito.admin_delete_user.assert_called_once_with(UserPoolId=self.settings.cognito_user_pool_id, Username="orphan-old")

    def test_orphan_erasure_intent_blocks_stale_callback_even_when_cognito_fails(self):
        subject = "synthetic-expired-signup"
        cognito = Mock()
        cognito.exceptions.UserNotFoundException = type("UserNotFound", (Exception,), {})
        cognito.get_paginator.return_value.paginate.return_value = [{"Users": [{"Username": subject, "Attributes": [{"Name": "sub", "Value": subject}], "UserCreateDate": utcnow() - timedelta(days=31)}]}]
        cognito.admin_delete_user.side_effect = RuntimeError("simulated response timeout")
        self.assertEqual(privacy_service.purge_orphaned_cognito_accounts(self.db, cognito)["failed"], 1)
        with self.assertRaises(HTTPException) as blocked:
            auth._issue_session_for_verified_claims(self.db, {"sub": subject, "email": "synthetic-old@example.test"})
        self.assertEqual(blocked.exception.status_code, 403)
        self.db.rollback()
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Parent).where(Parent.cognito_sub == subject)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DeletionLedger).where(DeletionLedger.subject_hash == security.token_hash(subject))), 1)
        cognito.admin_delete_user.side_effect = None
        self.assertEqual(privacy_service.purge_orphaned_cognito_accounts(self.db, cognito)["deleted"], 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DeletionLedger).where(DeletionLedger.subject_hash == security.token_hash(subject))), 1)

    def test_repeated_verified_signin_reuses_parent_and_rotates_session(self):
        claims = {"sub": "new-synthetic-parent", "email": "synthetic-new@example.test"}
        first = auth._issue_session_for_verified_claims(self.db, claims)
        second = auth._issue_session_for_verified_claims(self.db, claims)
        self.assertNotEqual(first, second)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Parent).where(Parent.cognito_sub == claims["sub"])), 1)
        parent_id = self.db.scalar(select(Parent.id).where(Parent.cognito_sub == claims["sub"]))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ParentSession).where(ParentSession.parent_id == parent_id)), 2)

    def test_identity_lock_is_transaction_scoped_and_does_not_send_raw_subject(self):
        db = Mock()
        db.get_bind.return_value.dialect.name = "postgresql"
        privacy_service.lock_cognito_subject(db, "subject-with-private-context")
        statement, params = db.execute.call_args.args
        self.assertEqual(str(statement), "SELECT pg_advisory_xact_lock(:identity_lock)")
        self.assertIsInstance(params["identity_lock"], int)
        self.assertNotIn("subject-with-private-context", str(statement) + str(params))
        privacy_service.lock_cognito_subject(db, "subject-with-private-context")
        self.assertEqual(db.execute.call_args_list[0].args[1], db.execute.call_args_list[1].args[1])
        db.commit.assert_not_called()
        db.reset_mock()
        db.get_bind.return_value.dialect.name = "sqlite"
        privacy_service.lock_cognito_subject(db, "synthetic-sqlite-subject")
        db.execute.assert_not_called()

    def test_logging_in_does_not_extend_unverified_registration_retention(self):
        self.parent.created_at = utcnow() - timedelta(days=31)
        self.parent.last_active_at = utcnow()
        self.db.commit()
        self.assertEqual(privacy_service.retention_sweep(self.db)["expired_accounts_queued"], 1)
        self.db.refresh(self.parent)
        self.assertEqual(self.parent.status, "deletion_pending")


class MigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_isolated_schema(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            self.assertEqual(set(inspect(connection).get_table_names()) - {"alembic_version"}, set(Base.metadata.tables))
            command.check(config)
            command.downgrade(config, "base")
            self.assertEqual(inspect(connection).get_table_names(), ["alembic_version"])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
