from __future__ import annotations

import csv
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from io import StringIO
from typing import Optional

from taxedo.config import Config
from taxedo.security import csv_value
from taxedo.bot.reporting import currency_totals, grouped_totals
from taxedo.storage.clarifications import PENDING_CLARIFICATIONS_TABLE
from taxedo.tax.categories import TAX_CATEGORIES, can_be_deductible

logger = logging.getLogger(__name__)
config = Config()

CSV_COLUMNS = [
    "id",
    "created_at",
    "receipt_date",
    "sender_id",
    "sender_name",
    "message_type",
    "store_name",
    "total_amount",
    "currency",
    "tax_category",
    "tax_subcategory",
    "tax_deductible",
    "deduction_notes",
    "items",
    "caption",
    "raw_text",
    "file_path",
    "file_hash",
    "needs_review",
    "tax_year",
    "knowledge_version",
    "source_build_date",
    "analysis_model",
    "clarification_policy",
    "rag_sources",
    "evidence",
    "review_status",
    "reviewer_id",
    "reviewed_at",
    "review_reason",
]


class ReceiptStore:
    def __init__(self, data_dir=None):
        self.data_dir = data_dir or config.DATA_DIR
        self.csv_path = self.data_dir / "all_receipts.csv"
        self.db_path = self.data_dir / "receipts.db"

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def init(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipt_messages (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    receipt_id INTEGER NOT NULL REFERENCES receipts(id),
                    PRIMARY KEY(chat_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS receipt_files (
                    file_hash TEXT PRIMARY KEY,
                    receipt_id INTEGER NOT NULL REFERENCES receipts(id)
                );
                CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS review_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id INTEGER NOT NULL,
                    reviewer_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """
            )
            connection.execute(PENDING_CLARIFICATIONS_TABLE)
            connection.execute("BEGIN IMMEDIATE")
            # The original release stored receipts in all_receipts.csv.
            if not connection.execute(
                "SELECT 1 FROM migrations WHERE name='csv-v1'"
            ).fetchone():
                for row in self._read_legacy_csv():
                    connection.execute(
                        "INSERT INTO receipts(id, payload) VALUES (?, ?)",
                        (row["id"], json.dumps(row, ensure_ascii=False)),
                    )
                    if row.get("file_hash"):
                        connection.execute(
                            "INSERT OR IGNORE INTO receipt_files VALUES (?,?)",
                            (row["file_hash"], row["id"]),
                        )
                connection.execute("INSERT INTO migrations VALUES ('csv-v1')")

    def _read_legacy_csv(self):
        if not self.csv_path.exists():
            return []
        with self.csv_path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        for row in rows:
            row["id"] = int(row["id"])
            row["total_amount"] = (
                float(row["total_amount"]) if row.get("total_amount") else None
            )
            row["tax_deductible"] = row.get("tax_deductible", "").lower() in (
                "yes",
                "true",
                "1",
            )
        return rows

    def insert_receipt(self, **kwargs) -> int:
        message_keys = kwargs.pop("message_keys", [])
        pending_key = kwargs.pop("pending_key", None)
        kwargs.setdefault(
            "created_at", datetime.now().isoformat(sep=" ", timespec="seconds")
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt_id = None
            for chat_id, message_id in message_keys:
                found = connection.execute(
                    "SELECT receipt_id FROM receipt_messages "
                    "WHERE chat_id=? AND message_id=?",
                    (chat_id, message_id),
                ).fetchone()
                if found:
                    receipt_id = found[0]
                    break
            file_hash = kwargs.get("file_hash")
            if file_hash:
                found = connection.execute(
                    "SELECT receipt_id FROM receipt_files WHERE file_hash=?",
                    (file_hash,),
                ).fetchone()
                if found:
                    receipt_id = found[0]
            if receipt_id is None:
                cursor = connection.execute(
                    "INSERT INTO receipts(payload) VALUES (?)",
                    (json.dumps(kwargs, ensure_ascii=False, allow_nan=False),),
                )
                receipt_id = cursor.lastrowid
            if file_hash:
                connection.execute(
                    "INSERT OR IGNORE INTO receipt_files VALUES (?, ?)",
                    (file_hash, receipt_id),
                )
            for chat_id, message_id in message_keys:
                connection.execute(
                    "INSERT OR IGNORE INTO receipt_messages VALUES (?, ?, ?)",
                    (chat_id, message_id, receipt_id),
                )
            if pending_key:
                connection.execute(
                    "DELETE FROM pending_clarifications "
                    "WHERE chat_id=? AND user_id=?",
                    pending_key,
                )
        return receipt_id

    def find_by_message(self, chat_id: int, message_id: int):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_id FROM receipt_messages "
                "WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            ).fetchone()
        return self.get_receipt_by_id(row[0]) if row else None

    def link_message(self, receipt_id, chat_id, message_id):
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO receipt_messages(chat_id,message_id,receipt_id) VALUES (?,?,?)",
                (chat_id, message_id, receipt_id),
            )

    def edit_analysis(self, receipt_id, reviewer_id, analysis, context, expected):
        """Replace model assessment in place; preserve attachment and audit changes."""
        if analysis.get("analysis_error") or analysis.get("needs_clarification"):
            raise ValueError("The updated assessment is incomplete; receipt unchanged.")
        fields = ("store_name", "items", "total_amount", "currency", "receipt_date",
                  "tax_category", "tax_subcategory", "deduction_notes", "rag_sources",
                  "evidence", "analysis_model", "clarification_policy", "knowledge_version",
                  "source_build_date", "tax_year")
        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if not row:
                raise ValueError("Receipt not found")
            before = json.loads(row[0])
            if dict(before, id=receipt_id) != expected:
                raise ValueError("This receipt changed while I was checking it. Please send your correction again.")
            after = dict(before)
            after.update({key: analysis[key] for key in fields if key in analysis})
            after.update(raw_text=context, needs_review=True, tax_deductible=False,
                         review_status="pending", reviewer_id=None, reviewed_at=None, review_reason=None)
            connection.execute("UPDATE receipts SET payload=? WHERE id=?", (json.dumps(after, ensure_ascii=False, allow_nan=False), receipt_id))
            connection.execute(
                "INSERT INTO review_events(receipt_id,reviewer_id,action,before_json,after_json,reason,created_at) VALUES (?,?,?,?,?,?,?)",
                (receipt_id, reviewer_id, "edited", json.dumps(before, ensure_ascii=False),
                 json.dumps(after, ensure_ascii=False), "User supplied a correction or additional context; assessment refreshed.", now),
            )
        return dict(after, id=receipt_id)

    def get_all_receipts(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM receipts ORDER BY id"
            ).fetchall()
        return [
            dict(json.loads(payload), id=receipt_id) for receipt_id, payload in rows
        ]

    def get_receipts_by_month(self, year: int, month: int) -> list[dict]:
        prefix = f"{year}-{month:02d}"
        return [
            r for r in self.get_all_receipts() if r.get("created_at", "").startswith(prefix)
        ]

    def get_receipts_last_n_days(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(sep=" ")
        return [r for r in self.get_all_receipts() if r.get("created_at", "") >= cutoff]

    def get_receipts_by_year(self, year: int) -> list[dict]:
        def receipt_year(receipt):
            value = receipt.get("tax_year")
            if value:
                return str(value)
            date = receipt.get("receipt_date") or receipt.get("created_at") or ""
            return date[:4]

        return [
            r for r in self.get_all_receipts() if receipt_year(r) == str(year)
        ]

    def get_receipt_by_id(self, receipt_id: int) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM receipts WHERE id=?", (receipt_id,)
            ).fetchone()
        return dict(json.loads(row[0]), id=receipt_id) if row else None

    def pending_reviews(self, limit: int = 20) -> list[dict]:
        return [
            r
            for r in self.get_all_receipts()
            if r.get("needs_review") and r.get("review_status", "pending") == "pending"
        ][:limit]

    def review_receipt(
        self,
        receipt_id: int,
        reviewer_id: int,
        action: str,
        reason: str,
        *,
        deductible: bool = False,
        category: str | None = None,
        subcategory: str | None = None,
    ) -> dict:
        if action not in {"approved", "corrected", "rejected", "cancelled"}:
            raise ValueError("Invalid review action")
        if not reason.strip() or len(reason) > 500:
            raise ValueError("Review reason must contain 1–500 characters")
        if action == "corrected" and (
            category not in TAX_CATEGORIES
            or subcategory not in TAX_CATEGORIES[category]["subcategories"]
        ):
            raise ValueError("Unknown category or subcategory")
        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM receipts WHERE id=?", (receipt_id,)
            ).fetchone()
            if not row:
                raise ValueError("Receipt not found")
            before = json.loads(row[0])
            if (
                not before.get("needs_review")
                or before.get("review_status", "pending") != "pending"
            ):
                raise ValueError("Receipt is not pending review")
            after = dict(before)
            if action == "corrected":
                after.update(tax_category=category, tax_subcategory=subcategory)
            if (
                deductible
                and action in {"approved", "corrected"}
                and not can_be_deductible(
                    after.get("tax_category"), after.get("tax_subcategory")
                )
            ):
                raise ValueError(
                    "Only a deductible category can be confirmed as deductible. "
                    "Use /correct with a deductible category, or answer no."
                )
            after.update(
                review_status=action,
                reviewer_id=reviewer_id,
                reviewed_at=now,
                review_reason=reason.strip(),
                needs_review=False,
                tax_deductible=bool(deductible)
                if action not in {"rejected", "cancelled"}
                else False,
            )
            connection.execute(
                "UPDATE receipts SET payload=? WHERE id=?",
                (json.dumps(after, ensure_ascii=False, allow_nan=False), receipt_id),
            )
            connection.execute(
                "INSERT INTO review_events("
                "receipt_id,reviewer_id,action,before_json,after_json,"
                "reason,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    reviewer_id,
                    action,
                    json.dumps(before, ensure_ascii=False),
                    json.dumps(after, ensure_ascii=False),
                    reason.strip(),
                    now,
                ),
            )
        return dict(after, id=receipt_id)

    def delete_receipt(self, receipt_id: int) -> Optional[str]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM receipts WHERE id=?", (receipt_id,)
            ).fetchone()
            if not row:
                return None
            file_path = json.loads(row[0]).get("file_path")
            connection.execute(
                "DELETE FROM receipt_messages WHERE receipt_id=?", (receipt_id,)
            )
            connection.execute(
                "DELETE FROM receipt_files WHERE receipt_id=?", (receipt_id,)
            )
            connection.execute(
                "UPDATE review_events SET before_json=?, after_json=?, reason='[redacted]' "
                "WHERE receipt_id=?",
                (
                    json.dumps({"redacted": True}),
                    json.dumps({"redacted": True}),
                    receipt_id,
                ),
            )
            connection.execute("DELETE FROM receipts WHERE id=?", (receipt_id,))
        return file_path

    def review_history(self, receipt_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT reviewer_id,action,reason,created_at FROM review_events "
                "WHERE receipt_id=? ORDER BY id",
                (receipt_id,),
            ).fetchall()
        return [
            dict(zip(("reviewer_id", "action", "reason", "created_at"), row))
            for row in rows
        ]

    def find_by_hash(self, file_hash: str) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_id FROM receipt_files WHERE file_hash=?",
                (file_hash,),
            ).fetchone()
        return self.get_receipt_by_id(row[0]) if row else None

    def get_stats(self) -> dict:
        rows = self.get_all_receipts()
        if not rows:
            return {
                "total_receipts": 0,
                "totals_by_currency": {},
                "first_date": None,
                "last_date": None,
                "top_stores": [],
                "top_categories": [],
            }

        totals = currency_totals(rows)
        dates = [r["created_at"] for r in rows if r.get("created_at")]

        store_counts: dict[str, int] = {}
        for r in rows:
            s = r.get("store_name", "")
            if s:
                store_counts[s] = store_counts.get(s, 0) + 1
        top_stores = sorted(store_counts.items(), key=lambda x: -x[1])[:5]

        cat_amounts = grouped_totals(
            (r for r in rows if r.get("tax_deductible") and r.get("tax_category")),
            "tax_category",
            "Uncategorized",
        )
        top_categories = []
        for currency in totals:
            ranked = sorted(
                (
                    (cat, amounts[currency])
                    for cat, amounts in cat_amounts.items()
                    if currency in amounts
                ),
                key=lambda item: -item[1],
            )[:5]
            top_categories.extend((cat, currency, amount) for cat, amount in ranked)

        return {
            "total_receipts": len(rows),
            "totals_by_currency": totals,
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
            "top_stores": top_stores,
            "top_categories": top_categories,
        }

    def get_stats_for_year(self, year: int) -> dict:
        rows = self.get_receipts_by_year(year)
        return {
            "total_receipts": len(rows),
            "totals_by_currency": currency_totals(rows),
            "deductible_by_currency": currency_totals(
                r for r in rows if r.get("tax_deductible")
            ),
        }

    def receipts_to_csv_string(self, receipts: list[dict]) -> str:
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in receipts:
            row = dict(r)
            row["tax_deductible"] = "Yes" if r.get("tax_deductible") else "No"
            row = {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict))
                else value
                for key, value in row.items()
            }
            writer.writerow({key: csv_value(value) for key, value in row.items()})
        return output.getvalue()
