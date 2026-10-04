"""Synthetic-only tests; never contact AWS or read the host environment file."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "configure-account-environment.py"
SPEC = importlib.util.spec_from_file_location("configure_account_environment", SCRIPT)
configure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure)


def synthetic_configuration():
    return {
        "ACCOUNTS_ENABLED": "false", "ACCOUNT_REGISTRATION_OPEN": "false", "CHILD_DATA_COLLECTION_ENABLED": "false",
        "ALLOW_INSECURE_LOCALHOST": "false", "PUBLIC_ORIGIN": "https://practice.example.test",
        "SESSION_SECRET": "synthetic-session-secret-not-for-production-123456789",
        "DATABASE_URL": "postgresql+psycopg://reading_sound_app@synthetic.us-west-2.rds.amazonaws.com:5432/reading_sound_game",
        "DATABASE_IAM_AUTH": "true", "DATABASE_SSL_ROOT_CERT": "/app/certs/rds-global-bundle.pem",
        "AWS_REGION": "us-west-2", "COGNITO_USER_POOL_ID": "us-west-2_Synthetic",
        "COGNITO_CLIENT_ID": "synthetic123", "COGNITO_CLIENT_SECRET": "synthetic-client-secret-123456789",
        "COGNITO_DOMAIN": "https://synthetic.auth.us-west-2.amazoncognito.com",
        "COGNITO_REDIRECT_URI": "https://practice.example.test/api/auth/callback",
    }


class ConfigureAccountEnvironmentTests(unittest.TestCase):
    def test_merge_preserves_azure_unrelated_settings_and_removes_duplicate_account_keys(self):
        values = synthetic_configuration()
        existing = "# Existing host settings\r\nAZURE_SPEECH_KEY=synthetic-azure-secret\r\nSITE_ADDRESS=practice.example.test\r\nACCOUNTS_ENABLED=true\nACCOUNTS_ENABLED=true\nCUSTOM_SETTING=keep-this"
        merged = configure.merge_environment(existing, values)
        self.assertIn("AZURE_SPEECH_KEY=synthetic-azure-secret\r\n", merged)
        self.assertIn("SITE_ADDRESS=practice.example.test\r\n", merged)
        self.assertIn("CUSTOM_SETTING=keep-this\n", merged)
        self.assertEqual(merged.count("ACCOUNTS_ENABLED="), 1)
        self.assertIn("ACCOUNTS_ENABLED='false'", merged)
        self.assertEqual(configure.merge_environment(merged, values), merged)

    def test_session_secret_cannot_change_without_explicit_rotation(self):
        existing = "SESSION_SECRET='synthetic-existing-secret'\nAZURE_SPEECH_KEY=keep\n"
        with self.assertRaises(configure.ConfigurationError):
            configure.merge_environment(existing, synthetic_configuration())
        self.assertIn("AZURE_SPEECH_KEY=keep\n", configure.merge_environment(existing, synthetic_configuration(), rotate_session_secret=True))

    def test_validation_rejects_malformed_json_duplicates_injection_and_enabled_flags(self):
        for raw in ("{invalid", "[]", '{"SESSION_SECRET":"one","SESSION_SECRET":"two"}'):
            with self.subTest(raw=raw), self.assertRaises(configure.ConfigurationError):
                configure.validate_configuration(raw, "us-west-2")
        bad_values = [{"ACCOUNTS_ENABLED": "true"}, {"CHILD_DATA_COLLECTION_ENABLED": True}, {"PRIVACY_CONTACT": "privacy@example.test\nAZURE_SPEECH_KEY=changed"}, {"SESSION_SECRET": "secret\x00"}, {"AZURE_SPEECH_KEY": "must-not-overwrite"}, {"DATABASE_IAM_AUTH": "false"}, {"ALLOW_INSECURE_LOCALHOST": "true"}, {"DATABASE_URL": "postgresql+psycopg://reading_sound_admin:secret@synthetic.rds.amazonaws.com:5432/reading_sound_game"}, {"PRIVACY_CONTACT": "bad'quote"}, {"BACKUP_MAX_RETENTION_DAYS": "90"}, {"PUBLIC_ORIGIN": "http://practice.example.test"}]
        bad_values.append({"PRIVACY_CONTACT": "privacy@example.test\u2028ACCOUNTS_ENABLED=true"})
        for changes in bad_values:
            with self.subTest(keys=list(changes)), self.assertRaises(configure.ConfigurationError):
                configure.validate_configuration(json.dumps({**synthetic_configuration(), **changes}), "us-west-2")

    def test_ssm_retrieval_requires_exact_securestring_and_hides_provider_errors(self):
        parameter = "/reading-sound-game/accounts/config"
        payload = json.dumps(synthetic_configuration())
        response = Mock(returncode=0, stdout=json.dumps({"Parameter": {"Name": parameter, "Type": "SecureString", "Value": payload}}))
        with patch.object(configure.subprocess, "run", return_value=response) as run:
            self.assertEqual(configure.fetch_configuration(parameter, "us-west-2"), payload)
            self.assertIn("--with-decryption", run.call_args.args[0])
            self.assertTrue(run.call_args.kwargs["capture_output"])
        for item in ({"Name": parameter, "Type": "String", "Value": payload}, {"Name": "/wrong", "Type": "SecureString", "Value": payload}):
            with patch.object(configure.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps({"Parameter": item}))), self.assertRaises(configure.ConfigurationError):
                configure.fetch_configuration(parameter, "us-west-2")
        with patch.object(configure.subprocess, "run", return_value=Mock(returncode=1, stderr="sensitive-provider-output")):
            with self.assertRaises(configure.ConfigurationError) as error:
                configure.fetch_configuration(parameter, "us-west-2")
            self.assertNotIn("sensitive-provider-output", str(error.exception))

    def test_dry_run_prints_only_key_names_and_does_not_write(self):
        values = synthetic_configuration()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / ".env"
            original = b"AZURE_SPEECH_KEY=existing-synthetic-secret\n"
            destination.write_bytes(original)
            output = io.StringIO()
            with patch.object(configure, "fetch_configuration", return_value=json.dumps(values)), contextlib.redirect_stdout(output):
                result = configure.main(["--destination", str(destination), "--dry-run"])
            self.assertEqual(result, 0)
            self.assertEqual(destination.read_bytes(), original)
            self.assertIn("SESSION_SECRET", output.getvalue())
            for value in (values["SESSION_SECRET"], values["COGNITO_CLIENT_SECRET"], "existing-synthetic-secret"):
                self.assertNotIn(value, output.getvalue())
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_atomic_write_failure_preserves_original_and_removes_temporary_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / ".env"
            destination.write_text("AZURE_SPEECH_KEY=synthetic-original\n", encoding="utf-8")
            with patch.object(configure.os, "replace", side_effect=OSError("synthetic replace failure")), self.assertRaises(OSError):
                configure.atomic_write(destination, "SESSION_SECRET=synthetic-new\n")
            self.assertEqual(destination.read_text(encoding="utf-8"), "AZURE_SPEECH_KEY=synthetic-original\n")
            self.assertEqual(list(Path(directory).iterdir()), [destination])
            configure.atomic_write(destination, "SESSION_SECRET=synthetic-new\n")
            self.assertEqual(destination.read_text(encoding="utf-8"), "SESSION_SECRET=synthetic-new\n")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
