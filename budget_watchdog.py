from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)
config = Config()

PRICING = {
    "claude-haiku-4-5-20251001":   {"input": 1.0 / 1_000_000, "output": 5.0 / 1_000_000},
    "claude-sonnet-4-20250514":    {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-sonnet-4-6-20260220":  {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-opus-4-6-20260220":    {"input": 5.0 / 1_000_000, "output": 25.0 / 1_000_000},
}

DEFAULT_PRICING = {"input": 1.0 / 1_000_000, "output": 5.0 / 1_000_000}


class BudgetWatchdog:

    def __init__(self, bot=None):
        self.db_path = config.DATA_DIR / "budget.db"
        self.bot = bot
        self._init_db()

        self.monthly_warn_threshold = config.BUDGET_MONTHLY_WARN
        self.total_warn_threshold = config.BUDGET_TOTAL_WARN

        self._monthly_alert_sent = False
        self._outage_alert_sent = False

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT NOT NULL DEFAULT (datetime('now')),
                model        TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd     REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def log_usage(self, model: str, input_tokens: int, output_tokens: int):
        pricing = PRICING.get(model, DEFAULT_PRICING)
        cost = (input_tokens * pricing["input"]) + (output_tokens * pricing["output"])

        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO api_usage (model, input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?)",
            (model, input_tokens, output_tokens, cost),
        )
        conn.commit()
        conn.close()

        logger.info(
            f"API usage: {model} | {input_tokens} in / {output_tokens} out | "
            f"${cost:.6f} | monthly: ${self.get_monthly_spend():.4f}"
        )

        return self._check_thresholds()

    def _check_thresholds(self) -> dict | None:
        monthly = self.get_monthly_spend()
        total = self.get_total_spend()

        if monthly >= self.monthly_warn_threshold and not self._monthly_alert_sent:
            self._monthly_alert_sent = True
            return {
                "type": "monthly_threshold",
                "monthly_spend": monthly,
                "threshold": self.monthly_warn_threshold,
                "total_spend": total,
            }

        if total >= self.total_warn_threshold:
            return {
                "type": "total_threshold",
                "total_spend": total,
                "threshold": self.total_warn_threshold,
                "monthly_spend": monthly,
            }

        return None

    def check_api_error(self, error) -> dict | None:
        error_str = str(error).lower()

        if "credit balance" in error_str and "too low" in error_str:
            if not self._outage_alert_sent:
                self._outage_alert_sent = True
                return {
                    "type": "out_of_credits",
                    "total_spend": self.get_total_spend(),
                    "monthly_spend": self.get_monthly_spend(),
                    "error": str(error),
                }

        if "rate_limit" in error_str or "overloaded" in error_str:
            return {
                "type": "rate_limited",
                "error": str(error),
            }

        return None

    def get_monthly_spend(self) -> float:
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) FROM api_usage
            WHERE strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
            """
        ).fetchone()
        conn.close()
        return row[0]

    def get_total_spend(self) -> float:
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM api_usage"
        ).fetchone()
        conn.close()
        return row[0]

    def get_monthly_calls(self) -> int:
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            """
            SELECT COUNT(*) FROM api_usage
            WHERE strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
            """
        ).fetchone()
        conn.close()
        return row[0]

    def get_stats(self) -> dict:
        conn = sqlite3.connect(str(self.db_path))
        total = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM api_usage"
        ).fetchone()
        monthly = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0), COUNT(*)
            FROM api_usage
            WHERE strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
            """
        ).fetchone()
        conn.close()
        return {
            "total_spend": total[0],
            "total_calls": total[1],
            "monthly_spend": monthly[0],
            "monthly_calls": monthly[1],
            "monthly_threshold": self.monthly_warn_threshold,
            "total_threshold": self.total_warn_threshold,
        }

    def reset_monthly_alert(self):
        self._monthly_alert_sent = False
        self._outage_alert_sent = False


def format_budget_alert(alert: dict) -> str:
    if alert["type"] == "out_of_credits":
        return (
            "<b>API CREDITS EXHAUSTED!</b>\n\n"
            "The Anthropic API returned: <i>credit balance too low</i>\n\n"
            f" Total spent: <b>${alert['total_spend']:.4f}</b>\n"
            f" This month: <b>${alert['monthly_spend']:.4f}</b>\n\n"
            "Receipt analysis is unavailable until credits are topped up.\n\n"
        )

    elif alert["type"] == "monthly_threshold":
        return (
            " <b>Monthly API spending alert</b>\n\n"
            f"This month's spend: <b>${alert['monthly_spend']:.4f}</b>\n"
            f"Threshold: ${alert['threshold']:.2f}\n"
            f"Total all-time: ${alert['total_spend']:.4f}\n\n"
            "This is just a heads-up — the bot is still working fine.\n"
            "At Haiku rates (~$0.003/receipt), this means ~"
            f"{int(alert['monthly_spend'] / 0.003)} receipts analyzed this month."
        )

    elif alert["type"] == "total_threshold":
        return (
            "<b>Total API spending alert</b>\n\n"
            f"All-time spend: <b>${alert['total_spend']:.4f}</b>\n"
            f"Threshold: ${alert['threshold']:.2f}\n"
            f"This month: ${alert['monthly_spend']:.4f}\n\n"
            "Just an FYI — everything is working normally."
        )

    elif alert["type"] == "rate_limited":
        return (
            " <b>API rate limited</b>\n\n"
            "The Anthropic API is temporarily overloaded or rate-limited.\n"
            "The bot will retry automatically. If this persists, receipts will return an error."
        )

    return f"Budget alert: {json.dumps(alert)}"
