"""Opt-in tests on a dedicated synthetic PostgreSQL database; never production.

Set RSG_TEST_POSTGRES_URL to the migrator/owner connection for a database named
reading_sound_game_test (or ending in _privacy_test). The test runner creates and
removes its own random schema; it never touches public application tables.
Optional RSG_TEST_POSTGRES_RUNTIME_URL checks a separately restricted runtime role
against the same synthetic database. IAM connections use the normal application
DATABASE_IAM_AUTH / DATABASE_SSL_ROOT_CERT / AWS_REGION settings and AWS identity.

Run: python -m unittest tests.test_postgres_accounts_integration -v
No Cognito/SES calls, real accounts, recordings, or child records are used.
"""
from __future__ import annotations

import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import ExitStack
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core import config, security
from app.database import Base, make_engine
from app.models.accounts import Attempt, Child, ConsentRecord, DeletionJob, Parent, ParentSession, PrivacyNotification, SpeechCheck, utcnow
from app.routes import auth
from app.services import privacy_service
from app.services.account_service import begin_speech_check, create_attempt, create_practice, get_owned_attempt, record_speech_result

TEST_URL = os.getenv("RSG_TEST_POSTGRES_URL", "")


@unittest.skipUnless(TEST_URL, "Set RSG_TEST_POSTGRES_URL for an isolated synthetic PostgreSQL integration run")
class PostgresAccountsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(TEST_URL)
        if url.get_backend_name() != "postgresql" or not url.database or not (url.database == "reading_sound_game_test" or url.database.endswith("_privacy_test")):
            raise ValueError("Integration tests require a dedicated database named reading_sound_game_test or ending in _privacy_test")
        if url.query.get("options"):
            raise ValueError("The test runner sets its own isolated search_path; do not supply connection options")
        cls.schema = "rsg_test_" + uuid4().hex
        cls.bootstrap_engine = make_engine(TEST_URL)
        with cls.bootstrap_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        scoped_url = url.update_query_dict({"options": f"-csearch_path={cls.schema},pg_catalog"})
        cls.engine = make_engine(scoped_url.render_as_string(hide_password=False))
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.engine.connect() as connection:
            if connection.scalar(text("SELECT current_schema()")) != cls.schema:
                raise RuntimeError("PostgreSQL test schema isolation did not take effect")
            connection.rollback()
            migration = Config("alembic.ini")
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")

    @classmethod
    def cleanup_schema(cls):
        cls.engine.dispose()
        # The name is generated here, never received from a request or environment.
        with cls.bootstrap_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{cls.schema}" CASCADE')
        cls.bootstrap_engine.dispose()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.settings = replace(config.settings, accounts_enabled=True, account_registration_open=False, child_data_collection_enabled=True,
                                database_url=TEST_URL, session_secret="synthetic-integration-session-secret-never-deploy",
                                public_origin="https://synthetic.example.test", cognito_domain="https://synthetic.auth.us-west-2.amazoncognito.com",
                                cognito_user_pool_id="us-west-2_synthetic", cognito_client_id="synthetic", cognito_client_secret="synthetic",
                                privacy_notice_version="synthetic-consent-v1", privacy_notification_from="")
        for module in (security, privacy_service):
            self.stack.enter_context(patch.object(module, "settings", self.settings))
        self.db = Session(self.engine, expire_on_commit=False)
        self.parents = [Parent(cognito_sub="synthetic-" + uuid4().hex, cognito_username="synthetic-" + uuid4().hex, email=f"synthetic-{index}@example.test", first_consent_verified_at=utcnow()) for index in range(2)]
        self.db.add_all(self.parents)
        self.db.flush()
        self.children = [Child(parent_id=parent.id, nickname=f"Synthetic {index}") for index, parent in enumerate(self.parents)]
        self.db.add_all(self.children)
        self.db.add_all([ConsentRecord(parent_id=parent.id, notice_version=self.settings.privacy_notice_version, status="verified") for parent in self.parents])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        # Empty only this test run's own generated schema, preserving all other schemas.
        tables = ", ".join(f'"{self.schema}"."{name}"' for name in Base.metadata.tables)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("TRUNCATE TABLE " + tables + " CASCADE")

    def start_attempt(self, index=0):
        parent, child = self.parents[index], self.children[index]
        practice = create_practice(self.db, parent, child.id, str(uuid4()))
        return create_attempt(self.db, parent, practice.id, str(uuid4()), {"level": "D", "sublevel": 1, "section": "standard", "exercise": "1", "line": "a"})

    def test_migration_matches_real_postgresql_schema(self):
        with self.engine.connect() as connection:
            self.assertEqual(set(inspect(connection).get_table_names()) - {"alembic_version"}, set(Base.metadata.tables))
            migration = Config("alembic.ini")
            migration.attributes["connection"] = connection
            command.check(migration)
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), "20261003_01")

    def test_real_foreign_keys_cascade_and_parent_isolation(self):
        attempt = self.start_attempt()
        with self.assertRaises(HTTPException) as denied:
            get_owned_attempt(self.db, self.parents[1], attempt.id)
        self.assertEqual(denied.exception.status_code, 404)
        self.db.rollback()
        begin_speech_check(self.db, self.parents[0], attempt.id, "presence")
        record_speech_result(self.db, self.parents[0], attempt.id, "presence", {"speechDetected": True, "correct": True})
        self.assertIsNone(attempt.correct)
        job = privacy_service.queue_deletion(self.db, self.parents[0], "child", self.children[0].id)
        self.assertEqual(privacy_service.process_deletions(self.db), {"completed": 1, "failed": 0})
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Attempt)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SpeechCheck)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Child).where(Child.parent_id == self.parents[1].id)), 1)
        self.assertEqual(self.db.get(DeletionJob, job.id).status, "complete")

    def test_concurrent_idempotent_starts_produce_one_session(self):
        request_id = str(uuid4())
        parent_id, child_id = self.parents[0].id, self.children[0].id
        barrier = threading.Barrier(2)

        def start():
            with Session(self.engine, expire_on_commit=False) as db:
                parent = db.get(Parent, parent_id)
                barrier.wait(timeout=5)
                return create_practice(db, parent, child_id, request_id).id

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(start) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(results[0], results[1])

    def test_concurrent_first_signins_create_one_parent(self):
        claims = {"sub": "synthetic-first-" + uuid4().hex, "email": "synthetic-first@example.test"}
        barrier = threading.Barrier(2)

        def signin():
            with Session(self.engine, expire_on_commit=False) as db:
                barrier.wait(timeout=5)
                return auth._issue_session_for_verified_claims(db, claims)

        with patch.object(auth, "settings", replace(self.settings, account_registration_open=True)):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(signin) for _ in range(2)]
                tokens = [future.result(timeout=10) for future in futures]
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Parent).where(Parent.cognito_sub == claims["sub"])), 1)
        parent_id = self.db.scalar(select(Parent.id).where(Parent.cognito_sub == claims["sub"]))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ParentSession).where(ParentSession.parent_id == parent_id)), 2)

    def test_orphan_cleanup_waits_for_first_signin_and_preserves_new_parent(self):
        subject = "synthetic-orphan-race-" + uuid4().hex
        cognito = Mock()
        cognito.exceptions.UserNotFoundException = type("UserNotFound", (Exception,), {})
        cognito.get_paginator.return_value.paginate.return_value = [{"Users": [{"Username": subject, "Attributes": [{"Name": "sub", "Value": subject}], "UserCreateDate": utcnow() - timedelta(days=31)}]}]
        started = threading.Event()

        def cleanup():
            with Session(self.engine, expire_on_commit=False) as db:
                db.execute(text("SET LOCAL statement_timeout = '5s'"))
                started.set()
                return privacy_service.purge_orphaned_cognito_accounts(db, cognito)

        privacy_service.lock_cognito_subject(self.db, subject)
        self.db.add(Parent(cognito_sub=subject, cognito_username=subject, email="synthetic-race@example.test"))
        self.db.flush()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(cleanup)
            self.assertTrue(started.wait(timeout=3))
            with self.assertRaises(TimeoutError):
                future.result(timeout=0.2)
            self.db.commit()
            self.assertEqual(future.result(timeout=5), {"deleted": 0, "failed": 0, "configured": True})
        cognito.admin_delete_user.assert_not_called()

    def test_orphan_cleanup_first_blocks_stale_verified_claims_before_remote_deletion(self):
        subject = "synthetic-expired-race-" + uuid4().hex
        deleting = threading.Event()
        finish_delete = threading.Event()
        cognito = Mock()
        cognito.exceptions.UserNotFoundException = type("UserNotFound", (Exception,), {})
        cognito.get_paginator.return_value.paginate.return_value = [{"Users": [{"Username": subject, "Attributes": [{"Name": "sub", "Value": subject}], "UserCreateDate": utcnow() - timedelta(days=31)}]}]

        def remote_delete(**kwargs):
            deleting.set()
            if not finish_delete.wait(timeout=5):
                raise TimeoutError("Test did not release synthetic provider")

        cognito.admin_delete_user.side_effect = remote_delete

        def cleanup():
            with Session(self.engine, expire_on_commit=False) as db:
                return privacy_service.purge_orphaned_cognito_accounts(db, cognito)

        with patch.object(auth, "settings", replace(self.settings, account_registration_open=True)):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(cleanup)
                try:
                    self.assertTrue(deleting.wait(timeout=3))
                    with self.assertRaises(HTTPException) as denied:
                        auth._issue_session_for_verified_claims(self.db, {"sub": subject, "email": "synthetic-expired@example.test"})
                    self.assertEqual(denied.exception.status_code, 403)
                    self.db.rollback()
                finally:
                    finish_delete.set()
                self.assertEqual(future.result(timeout=5)["deleted"], 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Parent).where(Parent.cognito_sub == subject)), 0)

    def test_parent_row_lock_serializes_deletion_and_rechecks_after_recognition(self):
        attempt = self.start_attempt()
        begin_speech_check(self.db, self.parents[0], attempt.id, "presence")
        parent_id, child_id = self.parents[0].id, self.children[0].id
        started = threading.Event()

        def request_deletion():
            with Session(self.engine, expire_on_commit=False) as db:
                db.execute(text("SET LOCAL statement_timeout = '5s'"))
                parent = db.get(Parent, parent_id)
                started.set()
                return privacy_service.queue_deletion(db, parent, "child", child_id).id

        get_owned_attempt(self.db, self.parents[0], attempt.id)  # Holds the parent row lock.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request_deletion)
            self.assertTrue(started.wait(timeout=3))
            with self.assertRaises(TimeoutError):
                future.result(timeout=0.2)
            self.db.rollback()  # Provider-await boundary must release the lock.
            future.result(timeout=5)
        with self.assertRaises(HTTPException) as denied:
            record_speech_result(self.db, self.parents[0], attempt.id, "presence", {"speechDetected": True})
        self.assertEqual(denied.exception.status_code, 404)
        self.db.rollback()
        self.assertEqual(privacy_service.process_deletions(self.db)["completed"], 1)

    def test_restore_replay_removes_contacts_and_all_restored_sessions(self):
        parent_id, email = self.parents[0].id, self.parents[0].email
        self.db.add(PrivacyNotification(event="consent_review_requested", reference=str(uuid4()), parent_email=email))
        for parent in self.parents:
            self.db.add(ParentSession(parent_id=parent.id, token_hash=security.token_hash(uuid4().hex), expires_at=utcnow() + timedelta(hours=1)))
        self.db.commit()
        privacy_service.queue_deletion(self.db, self.parents[0], "parent")
        privacy_service.replay_ledger(self.db, privacy_service.export_ledger(self.db))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Parent).where(Parent.id == parent_id)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(PrivacyNotification).where(PrivacyNotification.parent_email == email)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ParentSession)), 0)

    @unittest.skipUnless(os.getenv("RSG_TEST_POSTGRES_RUNTIME_URL"), "Optional restricted runtime-role URL not configured")
    def test_runtime_role_can_read_write_but_cannot_create_tables(self):
        runtime_url = make_url(os.environ["RSG_TEST_POSTGRES_RUNTIME_URL"])
        owner_url = make_url(TEST_URL)
        if (runtime_url.host, runtime_url.port, runtime_url.database) != (owner_url.host, owner_url.port, owner_url.database):
            raise ValueError("Runtime check must use the same dedicated synthetic database")
        if runtime_url.username == owner_url.username:
            raise ValueError("Runtime and migration usernames must be different")
        role = runtime_url.username
        if not role or not all(character.isalnum() or character == "_" for character in role):
            raise ValueError("Runtime role name is invalid")
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{self.schema}" TO "{role}"')
            connection.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{self.schema}" TO "{role}"')
        url = runtime_url.update_query_dict({"options": f"-csearch_path={self.schema},pg_catalog"})
        runtime_engine = make_engine(url.render_as_string(hide_password=False))
        try:
            with runtime_engine.begin() as connection:
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM parents")), 2)
                self.assertFalse(connection.scalar(text("SELECT has_schema_privilege(current_user, :schema, 'CREATE')"), {"schema": self.schema}))
                self.assertFalse(connection.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")))
                self.assertEqual(connection.execute(text("UPDATE children SET nickname='Synthetic updated' WHERE id=:id"), {"id": self.children[0].id}).rowcount, 1)
        finally:
            runtime_engine.dispose()
