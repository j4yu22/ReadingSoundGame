from __future__ import annotations

import unittest
import wave
import asyncio
from contextlib import ExitStack
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import get_practice_db, require_practice_parent
from app.main import app
from app.routes.speech import listen_check
from app.services.audio_clips import synthesize_token_clip
from app.services.text_to_speech import DialogueError


def recording(frames: int = 1600, channels: int = 1) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * frames * channels)
    return output.getvalue()


class SpeechPrivacyBoundaryTests(unittest.TestCase):
    """Synthetic audio only; these tests never call Azure, Cognito, or SES."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.saved_overrides = app.dependency_overrides.copy()
        self.addCleanup(self.restore_overrides)
        self.client = self.stack.enter_context(TestClient(app))
        self.parent = SimpleNamespace(id="test-parent")
        self.db = Mock()
        self.attempt = SimpleNamespace(id="test-attempt")
        self.stack.enter_context(patch("app.routes.speech.settings", replace(settings, accounts_enabled=True)))
        app.dependency_overrides[require_practice_parent] = lambda: self.parent
        app.dependency_overrides[get_practice_db] = lambda: self.db
        self.mocks = {
            name: self.stack.enter_context(patch(f"app.routes.speech.{name}"))
            for name in (
                "get_owned_attempt", "expected_for_attempt", "begin_speech_check",
                "record_speech_result", "record_speech_error", "recognize_wav_bytes",
            )
        }
        self.mocks["get_owned_attempt"].return_value = self.attempt
        self.mocks["expected_for_attempt"].return_value = "curriculum-answer"
        self.mocks["recognize_wav_bytes"].return_value = {
            "speechDetected": True, "correct": True,
            "recognizedText": "PRIVATE SPOKEN CONTENT",
            "words": [{"word": "PRIVATE SPOKEN CONTENT"}],
            "scores": {"accuracy": 91.0, "fluency": None, "unexpected": "PRIVATE"},
        }

    def restore_overrides(self):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.saved_overrides)

    def post_audio(self, body=None, **kwargs):
        return self.client.post(
            "/api/speech/listen-check?attempt_id=test-attempt&mode=final&expected=forged-answer",
            content=recording() if body is None else body,
            headers={"content-type": "audio/wav"}, **kwargs,
        )

    def test_only_server_answers_and_minimal_results_cross_boundary(self):
        response = self.post_audio()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "mode": "final", "speechDetected": True, "correct": True,
            "scores": {"accuracy": 91.0},
        })
        self.assertEqual(self.mocks["recognize_wav_bytes"].call_args.kwargs["expected"], "curriculum-answer")
        saved = self.mocks["record_speech_result"].call_args.args[-1]
        self.assertEqual(saved, response.json())
        self.assertNotIn("PRIVATE", str(saved))
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_unowned_attempt_never_reaches_provider(self):
        self.mocks["get_owned_attempt"].side_effect = HTTPException(404, "Not found.")
        self.assertEqual(self.post_audio().status_code, 404)
        self.mocks["begin_speech_check"].assert_not_called()
        self.mocks["recognize_wav_bytes"].assert_not_called()

    def test_no_consent_never_reaches_provider(self):
        def denied():
            raise HTTPException(403, "Verified parental consent is required.")
        app.dependency_overrides[require_practice_parent] = denied
        self.assertEqual(self.post_audio().status_code, 403)
        self.mocks["get_owned_attempt"].assert_not_called()
        self.mocks["recognize_wav_bytes"].assert_not_called()

    def test_duplicate_recording_never_reaches_provider(self):
        self.mocks["begin_speech_check"].side_effect = HTTPException(409, "Already submitted.")
        self.assertEqual(self.post_audio().status_code, 409)
        self.mocks["recognize_wav_bytes"].assert_not_called()

    def test_parent_lock_is_released_before_upload_and_consent_rechecked(self):
        async def stream():
            self.db.commit.assert_called_once()
            yield recording()

        request = SimpleNamespace(headers={"content-type": "audio/wav"}, stream=stream)
        self.mocks["begin_speech_check"].side_effect = HTTPException(403, "Consent withdrawn during upload.")
        with self.assertRaises(HTTPException) as denied:
            asyncio.run(listen_check(request, "test-attempt", "final", self.parent, self.db))
        self.assertEqual(denied.exception.status_code, 403)
        self.mocks["recognize_wav_bytes"].assert_not_called()

    def test_browser_authored_speech_clip_never_enters_persistent_cache(self):
        with patch("app.routes.speech.synthesize_token_clip", return_value=b"synthetic clip") as synthesize:
            response = self.client.post("/api/speech/token-clip", json={
                "token": "PRIVATE", "source_phrase": "PRIVATE", "tokens": ["PRIVATE"],
            })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(synthesize.call_args.kwargs["cache"])
        with patch("app.services.audio_clips.cache_path_for_clip") as cache_path, \
             patch("app.services.audio_clips.synthesize_wav_with_boundaries", return_value=(b"audio", [])), \
             patch("app.services.audio_clips.find_word_boundary_span", return_value=(None, None)), \
             patch("app.services.audio_clips.clip_wav_audio", return_value=b"clip"):
            self.assertEqual(synthesize_token_clip(["PRIVATE"], "PRIVATE", cache=False), b"clip")
            cache_path.assert_not_called()

    def test_invalid_empty_long_and_oversized_audio_rejected(self):
        for body in (b"not audio", recording(0), recording(16000 * 6), recording(100, channels=2)):
            with self.subTest(size=len(body)):
                self.assertEqual(self.post_audio(body).status_code, 400)
        self.assertEqual(self.post_audio(b"a" * (512 * 1024 + 1)).status_code, 413)
        self.mocks["recognize_wav_bytes"].assert_not_called()

    def test_provider_errors_are_not_exposed_or_marked_incorrect(self):
        self.mocks["recognize_wav_bytes"].side_effect = DialogueError("PRIVATE PROVIDER DETAILS")
        response = self.post_audio()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("PRIVATE", response.text)
        self.mocks["record_speech_error"].assert_called_once_with(self.db, self.parent, "test-attempt", "final")
        self.mocks["record_speech_result"].assert_not_called()

    def test_deletion_during_recognition_prevents_result_disclosure(self):
        self.mocks["record_speech_result"].side_effect = HTTPException(403, "Access withdrawn.")
        response = self.post_audio()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("accuracy", response.text)
        self.assertNotIn("PRIVATE", response.text)

    def test_validation_errors_do_not_echo_input(self):
        response = self.client.post("/api/speech/line", json={"line_id": {"private": "PRIVATE INPUT"}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("PRIVATE", response.text)

    def test_public_pages_have_security_headers_and_no_cross_origin_grants(self):
        response = self.client.get("/", headers={"origin": "https://unrelated.example"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertNotIn("access-control-allow-origin", response.headers)


if __name__ == "__main__":
    unittest.main()
