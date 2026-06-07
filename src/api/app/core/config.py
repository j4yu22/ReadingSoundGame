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


@dataclass(frozen=True)
class Settings:
    azure_speech_key: str = os.getenv("AZURE_SPEECH_KEY", "")
    azure_speech_region: str = os.getenv("AZURE_SPEECH_REGION", "")
    azure_speech_voice: str = os.getenv("AZURE_SPEECH_VOICE", "en-US-GuyNeural")
    azure_speech_format: str = os.getenv(
        "AZURE_SPEECH_FORMAT", "audio-24khz-48kbitrate-mono-mp3"
    )

    @property
    def tts_ready(self) -> bool:
        return bool(self.azure_speech_key and self.azure_speech_region)


settings = Settings()
