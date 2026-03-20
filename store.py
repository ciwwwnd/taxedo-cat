from __future__ import annotations

import csv
import fcntl
import json
import logging
from datetime import datetime, timedelta
from io import StringIO
from typing import Optional

from config import Config

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
]


class ReceiptStore:

    def __init__(self):
        self.csv_path = config.DATA_DIR / "all_receipts.csv"
        self._next_id = None

    def init(self):
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()
            logger.info(f"Created {self.csv_path}")

    def insert_receipt(self, **kwargs) -> int:
        receipt_id = self._get_next_id()
        kwargs["id"] = receipt_id
        kwargs["created_at"] = kwargs.get("created_at", datetime.now().isoformat(sep=" ", timespec="seconds"))
        kwargs["tax_deductible"] = "Yes" if kwargs.get("tax_deductible") else "No"

        items = kwargs.get("items", "")
        if isinstance(items, list):
            kwargs["items"] = "; ".join(
                f"{i.get('name', '?')} €{i.get('price', 0):.2f}" for i in items
            )
        elif isinstance(items, str):
            try:
                parsed = json.loads(items)
                if isinstance(parsed, list):
                    kwargs["items"] = "; ".join(
                        f"{i.get('name', '?')} €{i.get('price', 0):.2f}" for i in parsed
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        row = {col: kwargs.get(col, "") for col in CSV_COLUMNS}

        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writerow(row)
            fcntl.flock(f, fcntl.LOCK_UN)

        logger.info(f"Receipt #{receipt_id} saved to CSV")
        return receipt_id

    def _read_all(self) -> list[dict]:
        if not self.csv_path.exists():
            return []
        with open(self.csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        for r in rows:
            try:
                r["total_amount"] = float(r["total_amount"]) if r.get("total_amount") else None
            except (ValueError, TypeError):
                r["total_amount"] = None
            try:
                r["id"] = int(r["id"]) if r.get("id") else 0
            except (ValueError, TypeError):
                r["id"] = 0
            r["tax_deductible"] = r.get("tax_deductible", "").lower() in ("yes", "1", "true")
        return rows

    def get_all_receipts(self) -> list[dict]:
        return self._read_all()

    def get_receipts_by_month(self, year: int, month: int) -> list[dict]:
        prefix = f"{year}-{month:02d}"
        return [
            r for r in self._read_all()
            if r.get("created_at", "").startswith(prefix)
        ]

    def get_receipts_last_n_days(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(sep=" ")
        return [
            r for r in self._read_all()
            if r.get("created_at", "") >= cutoff
        ]

    def get_receipts_by_year(self, year: int) -> list[dict]:
        return [
            r for r in self._read_all()
            if r.get("created_at", "").startswith(str(year))
        ]

    def get_receipt_by_id(self, receipt_id: int) -> Optional[dict]:
        for r in self._read_all():
            if r.get("id") == receipt_id:
                return r
        return None

    def find_by_hash(self, file_hash: str) -> Optional[dict]:
        if file_hash.startswith("phash:"):
            import imagehash
            query = imagehash.hex_to_hash(file_hash[6:])
            for r in self._read_all():
                stored = r.get("file_hash", "")
                if stored.startswith("phash:"):
                    if query - imagehash.hex_to_hash(stored[6:]) <= 10:
                        return r
            return None
        for r in self._read_all():
            if r.get("file_hash") == file_hash:
                return r
        return None

    def get_stats(self) -> dict:
        rows = self._read_all()
        if not rows:
            return {
                "total_receipts": 0, "total_amount": 0,
                "first_date": None, "last_date": None,
                "top_stores": [], "top_categories": [],
            }

        total_amount = sum(r["total_amount"] or 0 for r in rows)
        dates = [r["created_at"] for r in rows if r.get("created_at")]

        store_counts: dict[str, int] = {}
        for r in rows:
            s = r.get("store_name", "")
            if s:
                store_counts[s] = store_counts.get(s, 0) + 1
        top_stores = sorted(store_counts.items(), key=lambda x: -x[1])[:5]

        cat_amounts: dict[str, float] = {}
        for r in rows:
            if r.get("tax_deductible") and r.get("tax_category"):
                cat = r["tax_category"]
                cat_amounts[cat] = cat_amounts.get(cat, 0) + (r["total_amount"] or 0)
        top_categories = sorted(cat_amounts.items(), key=lambda x: -x[1])[:5]

        return {
            "total_receipts": len(rows),
            "total_amount": total_amount,
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
            "top_stores": top_stores,
            "top_categories": top_categories,
        }

    def get_stats_for_year(self, year: int) -> dict:
        rows = self.get_receipts_by_year(year)
        total = sum(r["total_amount"] or 0 for r in rows)
        deductible = sum(
            r["total_amount"] or 0 for r in rows if r.get("tax_deductible")
        )
        return {
            "total_receipts": len(rows),
            "total_amount": total,
            "deductible_amount": deductible,
        }

    def receipts_to_csv_string(self, receipts: list[dict]) -> str:
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in receipts:
            row = dict(r)
            row["tax_deductible"] = "Yes" if r.get("tax_deductible") else "No"
            writer.writerow(row)
        return output.getvalue()

    def _get_next_id(self) -> int:
        if self._next_id is not None:
            self._next_id += 1
            return self._next_id

        rows = self._read_all()
        if rows:
            max_id = max(r.get("id", 0) for r in rows)
            self._next_id = max_id + 1
        else:
            self._next_id = 1
        return self._next_id
