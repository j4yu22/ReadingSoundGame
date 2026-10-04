from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = API_DIR.parent
REPO_ROOT = SRC_DIR.parent
WEB_DIR = SRC_DIR / "web"
SHARED_DIR = SRC_DIR / "shared"
DIALOGUE_PATH = SHARED_DIR / "dialogue" / "arthur.json"


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


def load_local_env() -> None:
    load_env_file(REPO_ROOT / ".env")
    load_env_file(REPO_ROOT / "test" / "arthur" / ".env")
    load_env_file(API_DIR / ".env")


load_local_env()


def env_bool(name: str) -> bool:
    return os.getenv(name, "false").lower() == "true"


@dataclass(frozen=True)
class Settings:
    accounts_enabled: bool = env_bool("ACCOUNTS_ENABLED")
    account_registration_open: bool = env_bool("ACCOUNT_REGISTRATION_OPEN")
    child_data_collection_enabled: bool = env_bool("CHILD_DATA_COLLECTION_ENABLED")
    database_url: str = os.getenv("DATABASE_URL", "")
    database_iam_auth: bool = env_bool("DATABASE_IAM_AUTH")
    database_ssl_root_cert: str = os.getenv("DATABASE_SSL_ROOT_CERT", "")
    public_origin: str = os.getenv("PUBLIC_ORIGIN", "http://127.0.0.1:5178").rstrip("/")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    allow_insecure_localhost: bool = env_bool("ALLOW_INSECURE_LOCALHOST")
    session_hours: int = int(os.getenv("SESSION_HOURS", "8"))
    cognito_region: str = os.getenv("COGNITO_REGION", os.getenv("AWS_REGION", "us-west-2"))
    cognito_user_pool_id: str = os.getenv("COGNITO_USER_POOL_ID", "")
    cognito_client_id: str = os.getenv("COGNITO_CLIENT_ID", "")
    cognito_client_secret: str = os.getenv("COGNITO_CLIENT_SECRET", "")
    cognito_domain: str = os.getenv("COGNITO_DOMAIN", "").rstrip("/")
    privacy_notice_version: str = os.getenv("PRIVACY_NOTICE_VERSION", "draft-2026-10-03")
    privacy_contact: str = os.getenv("PRIVACY_CONTACT", "gina_underwood@yahoo.com,jay.e.underwood@gmail.com")
    retention_days: int = int(os.getenv("PROGRESS_RETENTION_DAYS", "90"))
    deletion_ledger_days: int = int(os.getenv("DELETION_LEDGER_DAYS", "45"))
    backup_max_retention_days: int = int(os.getenv("BACKUP_MAX_RETENTION_DAYS", "35"))
    privacy_notification_emails: str = os.getenv("PRIVACY_NOTIFICATION_EMAILS", "gina_underwood@yahoo.com,jay.e.underwood@gmail.com")
    privacy_notification_from: str = os.getenv("PRIVACY_NOTIFICATION_FROM", "")
    azure_speech_key: str = os.getenv("AZURE_SPEECH_KEY", "")
    azure_speech_region: str = os.getenv("AZURE_SPEECH_REGION", "")
    azure_speech_voice: str = os.getenv("AZURE_SPEECH_VOICE", "en-US-AvaNeural")
    azure_speech_format: str = os.getenv(
        "AZURE_SPEECH_FORMAT", "audio-24khz-48kbitrate-mono-mp3"
    )

    @property
    def tts_ready(self) -> bool:
        return bool(self.azure_speech_key and self.azure_speech_region)

    @property
    def practice_mode(self) -> str:
        # Guest practice is an explicit operating mode, never a fallback when
        # account configuration, parental consent, or a database is unavailable.
        if not self.accounts_enabled:
            return "guest"
        if self.accounts_ready and self.child_data_collection_enabled:
            return "account"
        return "unavailable"

    @property
    def secure_cookies(self) -> bool:
        from urllib.parse import urlsplit
        parsed = urlsplit(self.public_origin)
        return not (self.allow_insecure_localhost and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})

    @property
    def accounts_ready(self) -> bool:
        from urllib.parse import urlsplit
        origin = urlsplit(self.public_origin)
        domain = urlsplit(self.cognito_domain)
        return bool(
            self.accounts_enabled and self.database_url.startswith("postgresql")
            and len(self.session_secret) >= 32 and self.cognito_user_pool_id
            and self.cognito_client_id and self.cognito_client_secret and domain.scheme == "https" and domain.hostname
            and not domain.path and not domain.query and not domain.fragment
            and origin.hostname and origin.path in {"", "/"} and not origin.query and not origin.fragment
            and (origin.scheme == "https" or not self.secure_cookies)
            and 1 <= self.session_hours <= 24 and self.privacy_contact
            and 1 <= self.retention_days <= 365 and self.deletion_ledger_days >= max(42, self.backup_max_retention_days + 7)
        )


settings = Settings()
