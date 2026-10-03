#!/usr/bin/env python3
"""Operator-only RDS bootstrap through an existing SSM localhost tunnel.

Dependencies: boto3, psycopg[binary], SQLAlchemy, Alembic and the API dependencies.
Never run as the website role. No credentials are written to disk or printed.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

DB_NAME = "reading_sound_game"
TEST_DB_NAME = "reading_sound_game_test"
MIGRATOR = "reading_sound_migrator"
RUNTIME = "reading_sound_app"
FLAGS = ("ACCOUNTS_ENABLED", "ACCOUNT_REGISTRATION_OPEN", "CHILD_DATA_COLLECTION_ENABLED")


def validate_instance(instance: dict) -> tuple[str, int, str]:
    if (instance.get("Engine") != "postgres" or instance.get("DBInstanceStatus") != "available"
            or instance.get("PubliclyAccessible") is not False or instance.get("StorageEncrypted") is not True
            or instance.get("IAMDatabaseAuthenticationEnabled") is not True or instance.get("DBName") != DB_NAME
            or instance.get("MasterUsername") != "reading_sound_admin"):
        raise ValueError("Instance does not match the reviewed private account database")
    endpoint = instance.get("Endpoint", {})
    host, port = endpoint.get("Address", ""), endpoint.get("Port")
    secret_arn = instance.get("MasterUserSecret", {}).get("SecretArn")
    if not host.endswith(".rds.amazonaws.com") or port != 5432 or not secret_arn:
        raise ValueError("Instance endpoint or managed master secret is unavailable")
    return host, port, secret_arn


def connection_options(host: str, local_port: int, ca: Path, user: str, password: str, database: str) -> dict:
    # libpq connects to hostaddr but verifies the certificate against host.
    return {"host": host, "hostaddr": "127.0.0.1", "port": local_port,
            "dbname": database, "user": user, "password": password,
            "sslmode": "verify-full", "sslrootcert": str(ca), "connect_timeout": 10}


def validate_roles(master) -> bool:
    """Return False only when neither role exists; refuse partial/unsafe state."""
    roles = master.execute(
        "SELECT rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname IN (%s, %s)", (MIGRATOR, RUNTIME),
    ).fetchall()
    if not roles:
        return False
    if len(roles) != 2 or any(not row[1] or any(row[2:]) for row in roles):
        raise ValueError("Partial or overly privileged database roles require operator review")
    for role in (MIGRATOR, RUNTIME):
        grants = master.execute(
            "SELECT pg_has_role(%s, 'rds_iam', 'MEMBER'), "
            "pg_has_role(%s, 'rds_superuser', 'MEMBER'), "
            "has_database_privilege(%s, current_database(), 'CONNECT'), "
            "has_schema_privilege(%s, 'public', 'USAGE'), has_schema_privilege(%s, 'public', 'CREATE')",
            (role, role, role, role, role),
        ).fetchone()
        if grants != (True, False, True, True, role == MIGRATOR):
            raise ValueError("Database role grants require operator review")
    if master.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", (RUNTIME, MIGRATOR)).fetchone()[0]:
        raise ValueError("Runtime must not inherit the migration role")
    return True


def test_connection_url(host: str, local_port: int, ca: Path, user: str, token: str) -> str:
    from sqlalchemy import URL
    return URL.create("postgresql+psycopg", username=user, password=token, host=host,
                      port=local_port, database=TEST_DB_NAME,
                      query={"hostaddr": "127.0.0.1", "sslmode": "verify-full", "sslrootcert": str(ca)}).render_as_string(hide_password=False)


def prepare_test_database(master):
    from psycopg import sql
    if not master.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TEST_DB_NAME,)).fetchone():
        master.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB_NAME)))
    master.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(TEST_DB_NAME)))
    master.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(sql.Identifier(TEST_DB_NAME), sql.Identifier(MIGRATOR), sql.Identifier(RUNTIME)))
    # Only the migrator can create the disposable integration schema.
    master.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(TEST_DB_NAME), sql.Identifier(MIGRATOR)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca", required=True, type=Path, help="Official RDS CA bundle file")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--db-instance", default="reading-sound-game-production-accounts")
    parser.add_argument("--local-port", default=5432, type=int)
    parser.add_argument("--test", action="store_true", help="Also create the dedicated synthetic DB and run the PostgreSQL integration suite")
    args = parser.parse_args(argv)
    stage = "preflight"
    try:
        import boto3
        import psycopg
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        root = Path(__file__).resolve().parents[1]
        api = root / "src" / "api"
        ca = args.ca.resolve(strict=True)
        if not ca.is_file() or not 1 <= args.local_port <= 65535:
            raise ValueError("Invalid tunnel or certificate configuration")
        if any(path.exists() for path in (root / ".env", root / "test" / "arthur" / ".env", api / ".env")):
            raise ValueError("Use the clean operator source bundle without application environment files")
        for flag in FLAGS:
            os.environ[flag] = "false"
        sys.path.insert(0, str(api))
        stage = "describe_database"
        rds = boto3.client("rds", region_name=args.region)
        instance = rds.describe_db_instances(DBInstanceIdentifier=args.db_instance)["DBInstances"][0]
        host, remote_port, secret_arn = validate_instance(instance)
        stage = "retrieve_managed_secret"
        secret = json.loads(boto3.client("secretsmanager", region_name=args.region).get_secret_value(SecretId=secret_arn)["SecretString"])
        if secret.get("username") != instance["MasterUsername"] or not isinstance(secret.get("password"), str) or not secret["password"]:
            raise ValueError("Managed secret identity is invalid")
        stage = "bootstrap_roles"
        with psycopg.connect(**connection_options(host, args.local_port, ca, secret["username"], secret["password"], DB_NAME), autocommit=True) as master:
            if not validate_roles(master):
                master.execute((root / "infra" / "accounts" / "bootstrap.sql").read_text(encoding="utf-8"))
                if not validate_roles(master):
                    raise RuntimeError("Bootstrap did not establish the reviewed roles")
            if args.test:
                prepare_test_database(master)
        secret.clear()
        stage = "migrate_schema"

        def migrator_connection():
            token = rds.generate_db_auth_token(DBHostname=host, Port=remote_port, DBUsername=MIGRATOR, Region=args.region)
            return psycopg.connect(**connection_options(host, args.local_port, ca, MIGRATOR, token, DB_NAME))

        engine = create_engine("postgresql+psycopg://", creator=migrator_connection, poolclass=NullPool, hide_parameters=True)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql((root / "infra" / "accounts" / "migrator-privileges.sql").read_text(encoding="utf-8"))
                connection.commit()
                migration = Config(str(api / "alembic.ini"))
                migration.attributes["connection"] = connection
                # Keep diagnostics in memory: no URLs, credentials, or provider
                # error text can escape through framework exception logging.
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    command.upgrade(migration, "head")
                    command.check(migration)
                connection.commit()
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
                if not revision:
                    raise RuntimeError("Schema revision is missing")
        finally:
            engine.dispose()
        stage = "verify_runtime_access"
        runtime_token = rds.generate_db_auth_token(DBHostname=host, Port=remote_port, DBUsername=RUNTIME, Region=args.region)
        with psycopg.connect(**connection_options(host, args.local_port, ca, RUNTIME, runtime_token, DB_NAME)) as runtime:
            check = runtime.execute("SELECT current_user, has_schema_privilege(current_user, 'public', 'CREATE'), (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())").fetchone()
            if check != (RUNTIME, False, True):
                raise RuntimeError("Runtime identity, privilege, or TLS verification failed")
            for table in ("parents", "children", "attempts", "deletion_jobs", "privacy_notifications"):
                grants = runtime.execute("SELECT bool_and(has_table_privilege(current_user, %s, privilege)) FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) AS permission(privilege)", (table,)).fetchone()[0]
                if not grants:
                    raise RuntimeError("Runtime table privileges are missing")
        tests_run = None
        if args.test:
            stage = "postgres_integration_tests"
            tokens = {user: rds.generate_db_auth_token(DBHostname=host, Port=remote_port, DBUsername=user, Region=args.region) for user in (MIGRATOR, RUNTIME)}
            environment = {**os.environ, "DATABASE_IAM_AUTH": "false", "RSG_TEST_POSTGRES_URL": test_connection_url(host, args.local_port, ca, MIGRATOR, tokens[MIGRATOR]), "RSG_TEST_POSTGRES_RUNTIME_URL": test_connection_url(host, args.local_port, ca, RUNTIME, tokens[RUNTIME])}
            result = subprocess.run([sys.executable, "-m", "unittest", "tests.test_postgres_accounts_integration", "-v"], cwd=api, env=environment, capture_output=True, text=True, timeout=120, check=False)
            tokens.clear()
            environment.pop("RSG_TEST_POSTGRES_URL", None)
            environment.pop("RSG_TEST_POSTGRES_RUNTIME_URL", None)
            match = re.search(r"Ran (\d+) tests?", result.stderr)
            tests_run = int(match.group(1)) if match else None
            if result.returncode or not tests_run or "skipped" in result.stderr:
                raise RuntimeError("PostgreSQL integration tests did not complete successfully")
        print(json.dumps({"ok": True, "bootstrap_validated": True, "schema_migrated": True, "runtime_tls_and_privileges_verified": True, "postgres_tests_run": tests_run}))
        return 0
    except Exception as error:
        # Exception messages and tracebacks can contain credentials. Type and
        # fixed stage labels are sufficient to identify the failed operation.
        print(json.dumps({"ok": False, "stage": stage, "error_type": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
