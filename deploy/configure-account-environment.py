#!/usr/bin/env python3
"""Prepare disabled account settings from one SSM SecureString JSON parameter.

Run on the EC2 host as root. Requires Python's standard library and the AWS CLI.
This command never starts services, changes Git, or manages deployment timers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

FLAGS = {"ACCOUNTS_ENABLED", "ACCOUNT_REGISTRATION_OPEN", "CHILD_DATA_COLLECTION_ENABLED"}
REQUIRED = FLAGS | {
    "PUBLIC_ORIGIN", "SESSION_SECRET", "ALLOW_INSECURE_LOCALHOST", "DATABASE_URL",
    "DATABASE_IAM_AUTH", "DATABASE_SSL_ROOT_CERT", "AWS_REGION", "COGNITO_USER_POOL_ID",
    "COGNITO_CLIENT_ID", "COGNITO_CLIENT_SECRET", "COGNITO_DOMAIN", "COGNITO_REDIRECT_URI",
}
ALLOWED = REQUIRED | {
    "COGNITO_REGION", "SESSION_HOURS", "PRIVACY_CONTACT", "PRIVACY_NOTICE_VERSION",
    "PRIVACY_NOTIFICATION_EMAILS", "PRIVACY_NOTIFICATION_FROM", "PROGRESS_RETENTION_DAYS",
    "DELETION_LEDGER_DAYS", "BACKUP_MAX_RETENTION_DAYS", "COMPOSE_PROFILES",
}
ENV_LINE = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$")


class ConfigurationError(Exception):
    """Messages must be fixed text or allowed key names; never secret values."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("JSON contains a duplicate key.")
        result[key] = value
    return result


def parse_json(raw: str):
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeError):
        raise ConfigurationError("Configuration is not valid JSON.") from None


def fetch_configuration(parameter: str, region: str) -> str:
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", parameter) or not re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-\d", region):
        raise ConfigurationError("Parameter name or AWS region is invalid.")
    try:
        response = subprocess.run(
            ["aws", "ssm", "get-parameter", "--name", parameter, "--with-decryption",
             "--region", region, "--output", "json", "--no-cli-pager"],
            capture_output=True, text=True, encoding="utf-8", timeout=40, check=False,
            env={**os.environ, "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off"},
        )
    except (OSError, subprocess.SubprocessError):
        raise ConfigurationError("Could not retrieve the account configuration from SSM.") from None
    if response.returncode != 0:
        # AWS stderr can contain request details; never forward it to output.
        raise ConfigurationError("Could not retrieve the account configuration from SSM.")
    envelope = parse_json(response.stdout)
    item = envelope.get("Parameter") if isinstance(envelope, dict) else None
    if not isinstance(item, dict) or item.get("Type") != "SecureString" or item.get("Name") != parameter or not isinstance(item.get("Value"), str):
        raise ConfigurationError("SSM did not return the requested SecureString configuration.")
    if len(item["Value"].encode("utf-8")) > 16384:
        raise ConfigurationError("Account configuration is too large.")
    return item["Value"]


def validate_configuration(raw: str, region: str) -> dict[str, str]:
    values = parse_json(raw)
    if not isinstance(values, dict) or set(values) - ALLOWED:
        raise ConfigurationError("Configuration must contain only approved account keys.")
    missing = REQUIRED - set(values)
    if missing:
        raise ConfigurationError("Missing required keys: " + ", ".join(sorted(missing)))
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 4096 or any(not 32 <= ord(char) <= 126 for char in value) or "'" in value or "\\" in value:
            raise ConfigurationError(key + " must be a single-line ASCII string without control characters, single quotes, or backslashes.")
    if any(values[key] != "false" for key in FLAGS) or values["ALLOW_INSECURE_LOCALHOST"] != "false":
        raise ConfigurationError("Preparation requires all collection flags and ALLOW_INSECURE_LOCALHOST to be exactly false.")
    if values["DATABASE_IAM_AUTH"] != "true":
        raise ConfigurationError("DATABASE_IAM_AUTH must be exactly true on the prepared host.")
    if values["AWS_REGION"] != region or values.get("COGNITO_REGION", region) != region:
        raise ConfigurationError("Configured AWS regions must match the retrieval region.")
    origin = urlsplit(values["PUBLIC_ORIGIN"])
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path or origin.query or origin.fragment:
        raise ConfigurationError("PUBLIC_ORIGIN must be an exact HTTPS origin without a trailing slash.")
    if values["COGNITO_REDIRECT_URI"] != values["PUBLIC_ORIGIN"] + "/api/auth/callback":
        raise ConfigurationError("COGNITO_REDIRECT_URI must match PUBLIC_ORIGIN and the callback route.")
    domain = urlsplit(values["COGNITO_DOMAIN"])
    if domain.scheme != "https" or not domain.hostname or not domain.hostname.endswith(f".auth.{region}.amazoncognito.com") or domain.username or domain.password or domain.path or domain.query or domain.fragment:
        raise ConfigurationError("COGNITO_DOMAIN must be a regional Cognito HTTPS origin.")
    if not re.fullmatch(re.escape(region) + r"_[A-Za-z0-9]+", values["COGNITO_USER_POOL_ID"]):
        raise ConfigurationError("COGNITO_USER_POOL_ID must belong to the configured region.")
    if not re.fullmatch(r"[A-Za-z0-9]+", values["COGNITO_CLIENT_ID"]) or len(values["COGNITO_CLIENT_SECRET"]) < 16:
        raise ConfigurationError("Cognito client credentials are incomplete.")
    if not 32 <= len(values["SESSION_SECRET"]) <= 256 or not re.fullmatch(r"[A-Za-z0-9_+/=.\-]+", values["SESSION_SECRET"]):
        raise ConfigurationError("SESSION_SECRET must be a 32-to-256-character generated secret.")
    database = urlsplit(values["DATABASE_URL"])
    try:
        valid_port = database.port == 5432
    except ValueError:
        valid_port = False
    if database.scheme != "postgresql+psycopg" or database.username != "reading_sound_app" or database.password is not None or not database.hostname or not database.hostname.endswith(".rds.amazonaws.com") or not valid_port or database.path != "/reading_sound_game" or database.query or database.fragment:
        raise ConfigurationError("DATABASE_URL must be the passwordless runtime IAM URL for the private RDS database.")
    if values["DATABASE_SSL_ROOT_CERT"] != "/app/certs/rds-global-bundle.pem":
        raise ConfigurationError("DATABASE_SSL_ROOT_CERT must use the image's reviewed RDS trust bundle.")
    for key, low, high, default in (("SESSION_HOURS", 1, 24, "8"), ("PROGRESS_RETENTION_DAYS", 1, 365, "90"), ("BACKUP_MAX_RETENTION_DAYS", 1, 365, "35"), ("DELETION_LEDGER_DAYS", 42, 730, "45")):
        value = values.get(key, default)
        if not value.isdecimal() or not low <= int(value) <= high:
            raise ConfigurationError(key + " is outside the supported range.")
    if int(values.get("DELETION_LEDGER_DAYS", "45")) < int(values.get("BACKUP_MAX_RETENTION_DAYS", "35")) + 7:
        raise ConfigurationError("Deletion ledger retention must exceed the backup horizon by at least seven days.")
    if values.get("COMPOSE_PROFILES", "") not in {"", "accounts"}:
        raise ConfigurationError("COMPOSE_PROFILES must be empty or accounts.")
    return values


def merge_environment(existing: str, values: dict[str, str], *, rotate_session_secret: bool = False) -> str:
    lines = existing.splitlines(keepends=True)
    old_secret = ""
    for line in lines:
        match = ENV_LINE.match(line.rstrip("\r\n"))
        if match and match[1] == "SESSION_SECRET":
            old_secret = match[2].strip().strip('"').strip("'")
    if old_secret and old_secret != values["SESSION_SECRET"] and not rotate_session_secret:
        raise ConfigurationError("SESSION_SECRET differs from the existing file; explicit --rotate-session-secret is required.")
    result, written = [], set()
    for line in lines:
        match = ENV_LINE.match(line.rstrip("\r\n"))
        if match and match[1] in values:
            key = match[1]
            if key not in written:
                # Single quotes keep dollar signs literal in Docker Compose.
                result.append(f"{key}='{values[key]}'\n")
                written.add(key)
        else:
            result.append(line)
    if result and not result[-1].endswith(("\n", "\r")):
        result.append("\n")
    result.extend(f"{key}='{values[key]}'\n" for key in sorted(set(values) - written))
    return "".join(result)


def atomic_write(destination: Path, contents: str) -> None:
    if destination.is_symlink():
        raise ConfigurationError("Refusing to replace a symbolic-link environment file.")
    fd, temporary = tempfile.mkstemp(prefix=".account-env-", dir=destination.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(contents.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        fd = -1
        os.replace(temporary, destination)
        if os.name == "posix":
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameter", default="/reading-sound-game/accounts/config")
    parser.add_argument("--destination", type=Path, default=Path("/opt/reading-sound-game/.env"))
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show approved key names; do not write files")
    parser.add_argument("--rotate-session-secret", action="store_true", help="Explicitly allow replacing a different existing SESSION_SECRET")
    args = parser.parse_args(argv)
    try:
        if not args.destination.is_absolute() or args.destination.is_symlink():
            raise ConfigurationError("Destination must be an absolute, nonsymlink file path.")
        values = validate_configuration(fetch_configuration(args.parameter, args.region), args.region)
        existing = args.destination.read_bytes().decode("utf-8") if args.destination.exists() else ""
        merged = merge_environment(existing, values, rotate_session_secret=args.rotate_session_secret)
        if not args.dry_run:
            if os.name != "posix" or os.geteuid() != 0:
                raise ConfigurationError("Apply requires root on the Linux host; use --dry-run for validation elsewhere.")
            atomic_write(args.destination, merged)
        print(("Validated keys: " if args.dry_run else "Prepared keys: ") + ", ".join(sorted(values)))
        return 0
    except ConfigurationError as exc:
        print("Account configuration was not applied: " + str(exc), file=sys.stderr)
    except (OSError, UnicodeError, ValueError):
        print("Account configuration could not complete safely; inspect host state without printing secret values.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
