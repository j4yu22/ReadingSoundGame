"""Anonymous practice uses synthetic speech and never connects to a database."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core import config, security
from app.main import app
from app.routes import accounts, speech
from app.services.text_to_speech import DialogueError
from tests.test_privacy_speech_boundary import recording


class GuestPracticeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.settings = replace(config.settings, accounts_enabled=False,
                                database_url="postgresql+psycopg://unused@localhost/unused")
        self.use_settings(self.settings)
        self.engine = self.stack.enter_context(patch("app.database.get_engine", side_effect=AssertionError("Guest opened database")))
        self.database = self.stack.enter_context(patch.object(security, "get_db", side_effect=AssertionError("Guest requested session")))
        self.persistence = [self.stack.enter_context(patch.object(speech, name)) for name in (
            "get_owned_attempt", "expected_for_attempt", "begin_speech_check",
            "record_speech_result", "record_speech_error",
        )]
        self.client = self.stack.enter_context(TestClient(app))

    def use_settings(self, settings):
        for module in (security, accounts, speech):
            self.stack.enter_context(patch.object(module, "settings", settings))

    def assert_no_account_access(self):
        self.database.assert_not_called()
        self.engine.assert_not_called()
        for operation in self.persistence:
            operation.assert_not_called()

    def listen(self, **params):
        return self.client.post("/api/speech/listen-check", params=params,
                                content=recording(), headers={"Content-Type": "audio/wav"})

    def test_guest_config_catalog_and_audio_need_no_account_or_database(self):
        response = self.client.get("/api/account/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["practice_mode"], "guest")
        self.assertFalse(response.json()["enabled"])
        self.assertFalse(response.json()["registration_open"])
        self.assertFalse(response.json()["child_collection_enabled"])
        self.assertEqual(self.client.get("/api/activities/catalog").status_code, 200)
        self.assertEqual(self.client.get("/api/speech/config").status_code, 200)
        with patch("app.routes.activities.prepare_activity", side_effect=lambda activity, _: activity):
            response = self.client.get("/api/activities/current", params={"level": "D", "sublevel": "1", "section": "standard", "exercise": "1", "line": "a"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["word"], "birthday")
        with patch.object(speech, "synthesize_azure_speech", return_value=b"dialogue"), \
             patch.object(speech, "get_dialogue_line", return_value={"ssml": "synthetic", "text": "hello"}):
            self.assertEqual(self.client.post("/api/speech/line", json={"line_id": "synthetic"}).content, b"dialogue")
        with patch.object(speech, "synthesize_token_clip", return_value=b"token") as synthesize:
            self.assertEqual(self.client.post("/api/speech/token-clip", json={"token": "day"}).content, b"token")
            self.assertFalse(synthesize.call_args.kwargs["cache"])
        with TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.wav"
            clip.write_bytes(b"curriculum clip")
            with patch("app.routes.activities.clip_path_for_id", return_value=clip):
                self.assertEqual(self.client.get("/api/activities/clips/synthetic").content, b"curriculum clip")
        self.assert_no_account_access()

    def test_guest_checks_are_transient_and_return_no_transcript(self):
        with patch.object(speech, "recognize_wav_bytes", return_value={
            "speechDetected": True, "correct": True, "recognizedText": "PRIVATE",
            "words": ["PRIVATE"], "scores": {"accuracy": 93, "private": "PRIVATE"},
        }) as recognize:
            for mode in ("presence", "final"):
                response = self.listen(mode=mode, expected="day", attempt_id="ignored-in-guest-mode")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"mode": mode, "speechDetected": True, "correct": True, "scores": {"accuracy": 93.0}})
                self.assertEqual(recognize.call_args.kwargs["expected"], "day")
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("set-cookie", response.headers)
        self.assert_no_account_access()

    def test_guest_errors_and_validation_never_persist_or_disclose_provider_details(self):
        with patch.object(speech, "recognize_wav_bytes", side_effect=DialogueError("PRIVATE")) as recognize:
            for params in ({"mode": "other"}, {"expected": "x" * 321}, {"expected": "hello\nworld"}):
                self.assertEqual(self.listen(**params).status_code, 400)
            self.assertEqual(self.client.post("/api/speech/listen-check", content=b"invalid", headers={"Content-Type": "audio/wav"}).status_code, 400)
            recognize.assert_not_called()
            response = self.listen(mode="final", expected="day")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("PRIVATE", response.text)
        self.assert_no_account_access()

    def test_enabled_but_incomplete_accounts_fail_closed_without_guest_fallback(self):
        self.use_settings(replace(self.settings, accounts_enabled=True, session_secret=""))
        self.assertEqual(self.client.get("/api/account/config").json()["practice_mode"], "unavailable")
        with patch.object(speech, "recognize_wav_bytes") as recognize:
            self.assertEqual(self.listen(mode="final", expected="day").status_code, 503)
            self.assertEqual(self.client.get("/api/speech/config").status_code, 503)
            self.assertEqual(self.client.get("/api/activities/current").status_code, 503)
            recognize.assert_not_called()
        self.assert_no_account_access()

    def test_only_explicitly_disabled_accounts_allow_guest_mode(self):
        ready = replace(self.settings, accounts_enabled=True, child_data_collection_enabled=True,
                        session_secret="synthetic-session-secret-1234567890123456", public_origin="https://practice.example",
                        cognito_domain="https://test.auth.us-west-2.amazoncognito.com", cognito_user_pool_id="us-west-2_test",
                        cognito_client_id="test-client", cognito_client_secret="test-secret")
        self.assertTrue(ready.accounts_ready)
        self.assertEqual(ready.practice_mode, "account")
        self.assertEqual(replace(ready, child_data_collection_enabled=False).practice_mode, "unavailable")
        self.assertEqual(replace(ready, accounts_enabled=False).practice_mode, "guest")


if __name__ == "__main__":
    unittest.main()
