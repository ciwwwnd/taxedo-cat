from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    GROUP_CHAT_ID: int | None = (
        int(os.getenv("GROUP_CHAT_ID")) if os.getenv("GROUP_CHAT_ID") else None
    )

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(Path(__file__).parent.parent / "data")))

    PARTNER_1_NAME: str = os.getenv("PARTNER_1_NAME", "Partner 1")
    PARTNER_2_NAME: str = os.getenv("PARTNER_2_NAME", "Partner 2")

    BUDGET_MONTHLY_WARN: float = float(os.getenv("BUDGET_MONTHLY_WARN", "0.50"))
    BUDGET_TOTAL_WARN: float = float(os.getenv("BUDGET_TOTAL_WARN", "5.00"))

    # Comma-separated Telegram user IDs allowed to use the bot, e.g.: 123456789,987654321
    ALLOWED_USER_IDS: list[int] = []

    def __init__(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        raw = os.getenv("ALLOWED_USER_IDS", "")
        if raw.strip():
            self.ALLOWED_USER_IDS = [
                int(uid.strip()) for uid in raw.split(",") if uid.strip()
            ]
