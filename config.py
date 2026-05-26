"""Load environment variables and data files. Fail fast with friendly messages."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUTS_DIR = ROOT / "outputs"
TEMPLATES_DIR = ROOT / "templates"


class ConfigError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Missing required file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc


class AppConfig:
    def __init__(self) -> None:
        load_dotenv(ROOT / ".env")

        self.base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        self.model = os.environ.get("OPENAI_MODEL", "").strip()
        self.app_referer = os.environ.get("APP_REFERER", "").strip()
        self.app_title = os.environ.get("APP_TITLE", "Resumer").strip()
        try:
            self.port = int(os.environ.get("PORT", "17321"))
        except ValueError:
            self.port = 17321

        self.settings: dict[str, Any] = _read_json(DATA_DIR / "settings.json")
        self.profile: dict[str, Any] = _read_json(DATA_DIR / "profile.json")
        self.limits: dict[str, Any] = _read_json(DATA_DIR / "limits.json")

        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    def validate_runtime(self) -> list[str]:
        """Return a list of human-readable problems blocking generation."""
        problems: list[str] = []
        if not self.api_key:
            problems.append("OPENAI_API_KEY is missing in .env")
        if not self.base_url:
            problems.append("OPENAI_BASE_URL is missing in .env")
        if not self.model:
            problems.append("OPENAI_MODEL is missing in .env")
        return problems


def load_config() -> AppConfig:
    try:
        return AppConfig()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        raise
