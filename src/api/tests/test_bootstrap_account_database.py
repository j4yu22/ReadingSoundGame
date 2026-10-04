"""Local synthetic tests for the operator helper; no AWS or database connections."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from sqlalchemy.engine import make_url

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "bootstrap-account-database.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_account_database", SCRIPT)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def synthetic_instance():
    return {"Engine": "postgres", "DBInstanceStatus": "available", "PubliclyAccessible": False,
            "StorageEncrypted": True, "IAMDatabaseAuthenticationEnabled": True,
            "DBName": "reading_sound_game", "MasterUsername": "reading_sound_admin",
            "Endpoint": {"Address": "synthetic.us-west-2.rds.amazonaws.com", "Port": 5432},
            "MasterUserSecret": {"SecretArn": "arn:synthetic:secret"}}


class BootstrapAccountDatabaseTests(unittest.TestCase):
    def test_instance_guard_rejects_public_wrong_or_incomplete_database(self):
        self.assertEqual(bootstrap.validate_instance(synthetic_instance())[1], 5432)
        for changed in ({"PubliclyAccessible": True}, {"StorageEncrypted": False}, {"IAMDatabaseAuthenticationEnabled": False}, {"DBName": "other"}, {"DBInstanceStatus": "creating"}, {"MasterUserSecret": {}}, {"MasterUsername": "unexpected-admin"}):
            with self.subTest(fields=list(changed)), self.assertRaises(ValueError):
                bootstrap.validate_instance({**synthetic_instance(), **changed})

    def test_tunnel_keeps_rds_hostname_for_tls_and_token_in_memory_only(self):
        host = synthetic_instance()["Endpoint"]["Address"]
        ca = Path("/synthetic/ca.pem")
        token = "synthetic:5432/?Action=connect&X-Amz-Credential=not-a-real-token"
        values = bootstrap.connection_options(host, 15432, ca, bootstrap.MIGRATOR, token, bootstrap.DB_NAME)
        self.assertEqual(values["host"], host)
        self.assertEqual(values["hostaddr"], "127.0.0.1")
        self.assertEqual(values["port"], 15432)
        self.assertEqual(values["sslmode"], "verify-full")
        url = make_url(bootstrap.test_connection_url(host, 15432, ca, bootstrap.RUNTIME, token))
        self.assertEqual(url.database, "reading_sound_game_test")
        self.assertEqual(url.password, token)
        self.assertEqual(url.query["hostaddr"], "127.0.0.1")
        self.assertEqual(url.query["sslmode"], "verify-full")

    def test_roles_allow_only_empty_or_fully_reviewed_state(self):
        master = Mock()
        master.execute.return_value.fetchall.return_value = []
        self.assertFalse(bootstrap.validate_roles(master))
        master.execute.return_value.fetchall.return_value = [(bootstrap.MIGRATOR, True, False, False, False, False, False)]
        with self.assertRaises(ValueError):
            bootstrap.validate_roles(master)
        roles = [(name, True, False, False, False, False, False) for name in (bootstrap.MIGRATOR, bootstrap.RUNTIME)]
        master.execute.return_value.fetchall.return_value = roles
        master.execute.return_value.fetchone.side_effect = [(True, False, True, True, True), (True, False, True, True, False), (False,)]
        self.assertTrue(bootstrap.validate_roles(master))
        master.execute.return_value.fetchone.side_effect = [(True, False, True, True, True), (True, False, True, True, True)]
        with self.assertRaises(ValueError):
            bootstrap.validate_roles(master)

    def test_exceptions_print_only_safe_stage_and_type(self):
        import boto3
        with tempfile.TemporaryDirectory() as directory:
            ca = Path(directory) / "ca.pem"
            ca.write_text("synthetic certificate placeholder", encoding="utf-8")
            exists = Path.exists
            stderr = io.StringIO()
            with patch.object(Path, "exists", lambda path: False if path.name == ".env" else exists(path)), patch.object(boto3, "client", side_effect=RuntimeError("never-print-this-synthetic-secret")), contextlib.redirect_stderr(stderr):
                result = bootstrap.main(["--ca", str(ca)])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(stderr.getvalue()), {"ok": False, "stage": "describe_database", "error_type": "RuntimeError"})
            self.assertNotIn("never-print", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
