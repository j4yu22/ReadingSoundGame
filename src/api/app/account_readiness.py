"""Read-only account service checks; never enables flags or creates user records.

Run from src/api: python -m app.account_readiness
Use --cognito-client separately with an operator's AWS credentials to verify the
private Cognito client configuration without connecting to PostgreSQL.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hmac
import json
import logging
from urllib.parse import urlsplit

import httpx
import jwt
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import event, inspect, text

from app.core.config import API_DIR, settings
from app.database import Base, make_engine
from app.models import accounts as _account_models  # Register the account tables.
from app.routes.auth import issuer


def database_checks() -> dict[str, bool]:
    """Inspect metadata and connection state only, using the application's role."""
    checks = dict.fromkeys((
        "connected", "tls", "tls_hostname_verified", "runtime_user_matches",
        "runtime_role_restricted", "schema_current", "tables_present",
        "runtime_table_permissions", "runtime_cannot_change_schema",
    ), False)
    engine = None
    try:
        engine = make_engine(settings.database_url)

        @event.listens_for(engine, "do_connect")
        def bounded_connect(dialect, record, args, params):
            params["connect_timeout"] = 10

        with engine.connect() as db:
            db.exec_driver_sql("SET TRANSACTION READ ONLY")
            db.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
            checks["connected"] = True
            checks["tls"] = bool(db.scalar(text(
                "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
            )))
            connection_info = db.connection.driver_connection.info
            checks["tls_hostname_verified"] = (
                connection_info.get_parameters().get("sslmode") == "verify-full"
            )
            checks["runtime_user_matches"] = db.scalar(text("SELECT current_user")) == engine.url.username
            checks["runtime_role_restricted"] = bool(db.scalar(text(
                "SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) "
                "FROM pg_roles WHERE rolname = current_user"
            )))
            config = Config(str(API_DIR / "alembic.ini"))
            expected = set(ScriptDirectory.from_config(config).get_heads())
            actual = set(MigrationContext.configure(db).get_current_heads())
            checks["schema_current"] = bool(expected) and actual == expected
            tables = set(Base.metadata.tables)
            checks["tables_present"] = tables <= set(inspect(db).get_table_names(schema="public"))
            if checks["tables_present"]:
                checks["runtime_table_permissions"] = all(
                    db.scalar(text("SELECT has_table_privilege(current_user, :table, :privilege)"),
                              {"table": "public." + table, "privilege": privilege})
                    for table in tables for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
                )
                owns_table = any(db.scalar(text(
                    "SELECT pg_has_role(current_user, relowner, 'USAGE') "
                    "FROM pg_class WHERE oid = to_regclass(:table)"
                ), {"table": "public." + table}) for table in tables)
                checks["runtime_cannot_change_schema"] = not owns_table and not db.scalar(text(
                    "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
                ))
    except Exception:
        # Driver/AWS exception messages may contain connection details or tokens.
        # Return failed checks instead of printing exception text or a traceback.
        pass
    finally:
        if engine is not None:
            engine.dispose()
    return checks


def public_cognito_checks() -> dict[str, bool]:
    checks = dict.fromkeys(("discovery_available", "issuer_matches", "endpoints_match", "signing_keys_available"), False)
    if not settings.cognito_user_pool_id or not settings.cognito_domain:
        return checks
    try:
        expected_issuer = issuer()
        expected_jwks = expected_issuer + "/.well-known/jwks.json"
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            discovery = client.get(expected_issuer + "/.well-known/openid-configuration")
            discovery.raise_for_status()
            data = discovery.json()
            checks["discovery_available"] = True
            checks["issuer_matches"] = data.get("issuer") == expected_issuer
            checks["endpoints_match"] = (
                data.get("jwks_uri") == expected_jwks
                and data.get("authorization_endpoint") == settings.cognito_domain + "/oauth2/authorize"
                and data.get("token_endpoint") == settings.cognito_domain + "/oauth2/token"
            )
            # Use the same issuer-derived JWKS URL as the authentication route.
            response = client.get(expected_jwks)
            response.raise_for_status()
            keys = jwt.PyJWKSet.from_json(response.text).keys
            checks["signing_keys_available"] = any(
                key.key_id and key.algorithm_name == "RS256" and key.public_key_use == "sig"
                for key in keys
            )
    except Exception:
        pass
    return checks


def private_cognito_checks() -> dict[str, bool]:
    """Separate operator check; runtime credentials need no client-read access."""
    import boto3
    from botocore.config import Config as AWSConfig

    checks = dict.fromkeys((
        "client_readable", "client_secret_matches", "callback_registered",
        "logout_registered", "authorization_code_enabled", "scopes_available",
        "cognito_provider_enabled", "managed_login_active",
    ), False)
    if not all((settings.cognito_user_pool_id, settings.cognito_client_id, settings.cognito_client_secret, settings.cognito_domain)):
        return checks
    try:
        client = boto3.client("cognito-idp", region_name=settings.cognito_region,
                              config=AWSConfig(connect_timeout=10, read_timeout=10, retries={"max_attempts": 1}))
        data = client.describe_user_pool_client(
            UserPoolId=settings.cognito_user_pool_id, ClientId=settings.cognito_client_id,
        )["UserPoolClient"]
        checks["client_readable"] = True
        checks["client_secret_matches"] = bool(settings.cognito_client_secret) and hmac.compare_digest(
            data.get("ClientSecret", ""), settings.cognito_client_secret,
        )
        checks["callback_registered"] = settings.public_origin + "/api/auth/callback" in data.get("CallbackURLs", [])
        checks["logout_registered"] = settings.public_origin + "/" in data.get("LogoutURLs", [])
        checks["authorization_code_enabled"] = (
            data.get("AllowedOAuthFlowsUserPoolClient") is True
            and set(data.get("AllowedOAuthFlows", [])) == {"code"}
        )
        checks["scopes_available"] = {"openid", "email"} <= set(data.get("AllowedOAuthScopes", []))
        checks["cognito_provider_enabled"] = "COGNITO" in data.get("SupportedIdentityProviders", [])
        host = urlsplit(settings.cognito_domain).hostname or ""
        suffix = ".auth." + settings.cognito_region + ".amazoncognito.com"
        domain = host[:-len(suffix)] if host.endswith(suffix) else host
        description = client.describe_user_pool_domain(Domain=domain)["DomainDescription"]
        checks["managed_login_active"] = (
            description.get("UserPoolId") == settings.cognito_user_pool_id
            and description.get("Status") == "ACTIVE"
            and description.get("ManagedLoginVersion") == 2
        )
    except Exception:
        pass
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cognito-client", action="store_true",
                        help="Only inspect private Cognito settings using an operator's AWS identity; skip PostgreSQL")
    args = parser.parse_args()
    # Keep SDK warnings and driver errors from leaking sensitive diagnostic data.
    logging.disable(logging.CRITICAL)
    if args.cognito_client:
        checks = {"cognito_client": private_cognito_checks()}
    else:
        # Evaluate the existing startup guard without changing the loaded settings.
        checks = {"configuration": {"ready_when_enabled": replace(settings, accounts_enabled=True).accounts_ready},
                  "database": database_checks(), "cognito_public": public_cognito_checks()}
    ready = all(value for group in checks.values() for value in group.values())
    output = {
        "scope": "cognito_client" if args.cognito_client else "runtime",
        "ready": ready,
        "checks": checks,
        "feature_flags": {"accounts": settings.accounts_enabled,
                          "registration": settings.account_registration_open,
                          "child_collection": settings.child_data_collection_enabled},
    }
    if not args.cognito_client:
        output["cognito_client_configuration"] = "not_checked; run --cognito-client with operator credentials"
    print(json.dumps(output, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
