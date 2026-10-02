from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv
from taxedo.paths import PROJECT_ROOT, DEFAULT_DATA_DIR

load_dotenv(PROJECT_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    GROUP_CHAT_ID: int | None = (
        int(os.getenv("GROUP_CHAT_ID")) if os.getenv("GROUP_CHAT_ID") else None
    )

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    CLAUDE_TEMPERATURE: float | None = (
        float(os.getenv("CLAUDE_TEMPERATURE")) if os.getenv("CLAUDE_TEMPERATURE") else None
    )
    AGENTIC_CLARIFICATION: bool = _env_bool("AGENTIC_CLARIFICATION", True)
    CLARIFICATION_TTL_MINUTES: int = int(os.getenv("CLARIFICATION_TTL_MINUTES", "30"))
    CLARIFICATION_MAX_QUESTIONS: int = int(
        os.getenv("CLARIFICATION_MAX_QUESTIONS", "2")
    )

    DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))

    API_TIMEOUT_SECONDS = 30
    ANALYSIS_TIMEOUT_SECONDS = 90

    def validate_startup(self):
        if not self.TELEGRAM_BOT_TOKEN or not self.ANTHROPIC_API_KEY:
            raise ValueError("Telegram and Anthropic credentials are required")
        if not self.ALLOWED_USER_IDS or not self.GROUP_CHAT_ID:
            raise ValueError("ALLOWED_USER_IDS and GROUP_CHAT_ID must be configured")

    def __init__(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.ALLOWED_USER_IDS = [
            int(value.strip())
            for value in os.getenv("ALLOWED_USER_IDS", "").split(",")
            if value.strip()
        ]
