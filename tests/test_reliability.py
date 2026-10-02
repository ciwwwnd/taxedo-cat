from __future__ import annotations

import asyncio
import csv
import signal
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import taxedo.bot.app as main
from taxedo.agent.analyzer import _validate_classification
from taxedo.storage.clarifications import ClarificationStore
from taxedo.ingestion.runner import extract_attachment
from taxedo.security import (
    authorized,
    check_upload,
    MAX_UPLOAD_BYTES,
    receipt_path,
    UserInputError,
)
from taxedo.storage.receipts import ReceiptStore, CSV_COLUMNS
from fakes import make_analyzer


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = ReceiptStore(self.root / "data")
        self.store.init()
        self.pending = ClarificationStore(self.store.db_path)

    def test_retry_and_restart_return_same_receipt(self):
        first = self.store.insert_receipt(
            message_keys=[(1, 2)], store_name="Store", tax_deductible=False
        )
        restarted = ReceiptStore(self.store.data_dir)
        restarted.init()
        second = restarted.insert_receipt(message_keys=[(1, 2)], store_name="Duplicate")
        self.assertEqual(first, second)
        self.assertEqual(len(restarted.get_all_receipts()), 1)

    def test_clarification_finalize_and_idempotency_are_atomic(self):
        self.pending.start(1, 2, "Expense", "Purpose?", {"message_id": 10})
        self.pending.record_answer(1, 2, "Work", 11)
        self.pending.record_answer(1, 2, "Work", 11)
        self.assertEqual(len(self.pending.get(1, 2).answers), 1)
        receipt_id = self.store.insert_receipt(
            message_keys=[(1, 10), (1, 11)], pending_key=(1, 2)
        )
        self.assertIsNone(self.pending.get(1, 2))
        self.assertEqual(self.store.find_by_message(1, 11)["id"], receipt_id)

    def test_file_duplicate_is_atomic_across_messages(self):
        first = self.store.insert_receipt(message_keys=[(1, 2)], file_hash="sha256")
        second = self.store.insert_receipt(message_keys=[(1, 3)], file_hash="sha256")
        self.assertEqual(first, second)
        self.assertEqual(self.store.find_by_message(1, 3)["id"], first)

    def test_transaction_rolls_back_on_finalize_failure(self):
        with self.assertRaises(sqlite3.ProgrammingError):
            self.store.insert_receipt(message_keys=[(1, 2)], pending_key=(1,))
        self.assertEqual(self.store.get_all_receipts(), [])
        self.assertIsNone(self.store.find_by_message(1, 2))

    def test_legacy_csv_migration_runs_once(self):
        directory = self.root / "legacy"
        directory.mkdir()
        with (directory / "all_receipts.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerow(
                dict(
                    id=42,
                    store_name="Legacy",
                    tax_deductible="Yes",
                    total_amount="12.34",
                )
            )
        store = ReceiptStore(directory)
        store.init()
        store.init()
        self.assertEqual(len(store.get_all_receipts()), 1)
        self.assertTrue(store.get_receipt_by_id(42)["tax_deductible"])
        self.assertEqual(store.insert_receipt(store_name="New"), 43)

    def test_delete_receipt_cascades_and_redacts_review_history(self):
        receipt_id = self.store.insert_receipt(
            message_keys=[(1, 2)],
            file_hash="sha256",
            store_name="Saved",
            needs_review=True,
            tax_category="Werbungskosten",
            tax_subcategory="Arbeitsmittel",
        )
        self.store.review_receipt(
            receipt_id, 99, "approved", "Looks fine", deductible=True
        )
        file_path = self.store.delete_receipt(receipt_id)
        self.assertIsNone(file_path)
        self.assertIsNone(self.store.get_receipt_by_id(receipt_id))
        self.assertIsNone(self.store.find_by_message(1, 2))
        self.assertIsNone(self.store.find_by_hash("sha256"))
        history = self.store.review_history(receipt_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reason"], "[redacted]")
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT before_json, after_json FROM review_events WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
        self.assertEqual(json.loads(row[0]), {"redacted": True})
        self.assertEqual(json.loads(row[1]), {"redacted": True})

    def test_csv_formulas_are_neutralized(self):
        exported = self.store.receipts_to_csv_string(
            [{"store_name": '=HYPERLINK("bad")', "total_amount": -5}]
        )
        row = next(csv.DictReader(StringIO(exported)))
        self.assertTrue(row["store_name"].startswith("'="))
        self.assertEqual(row["total_amount"], "-5")


class BoundaryTests(unittest.TestCase):
    def test_access_requires_user_and_chat(self):
        self.assertFalse(authorized(1, 2, [], 2))
        self.assertFalse(authorized(1, 2, [1], None))
        self.assertFalse(authorized(1, None, [1], None))
        self.assertFalse(authorized(1, 3, [1], 2))
        self.assertTrue(authorized(1, 2, [1], 2))

    def test_receipt_paths_cannot_escape_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                receipt_path(Path(directory), "files/../../secret")

    def test_upload_bounds(self):
        check_upload(50 * 1024 * 1024)
        check_upload(MAX_UPLOAD_BYTES)
        for size in (None, 0, MAX_UPLOAD_BYTES + 1):
            with self.assertRaises(ValueError):
                check_upload(size)

    def test_unknown_taxonomy_is_rejected(self):
        with self.assertRaises(ValueError):
            _validate_classification(
                dict(
                    tax_category="Made up",
                    tax_subcategory="anything",
                    tax_deductible=True,
                    deduction_notes="test",
                )
            )

    def test_html_is_escaped_and_review_is_visible(self):
        text = main.format_analysis_response(dict(
                store_name="<b>evil</b>",
                needs_review=True,
                tax_category="Werbungskosten",
                deduction_notes='<a href="bad">click</a>',
                items=[{"name": "<script>", "price": None}],
            ), 1)
        self.assertIn("&lt;b&gt;evil&lt;/b&gt;", text)
        self.assertIn("Pending review", text)
        self.assertNotIn("<script>", text)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_conversion_runs_in_worker(self):
        text = await extract_attachment(b"Item,Price\nMonitor,249\n", "receipt.csv")
        self.assertIn("Monitor", text)

    async def test_worker_timeout_terminates_process(self):
        started = []
        spawn = asyncio.create_subprocess_exec

        async def record(*args, **kwargs):
            started.append(await spawn(*args, **kwargs))
            return started[-1]

        with patch("asyncio.create_subprocess_exec", record):
            with self.assertRaises(asyncio.TimeoutError):
                await extract_attachment(
                    b"Item,Price\nMonitor,249\n", "receipt.csv", timeout=0.001
                )
        self.assertEqual(started[0].returncode, -signal.SIGKILL)

    async def test_malformed_pdf_fails_with_the_workers_reason(self):
        with self.assertRaisesRegex(UserInputError, "^File is not a PDF$"):
            await extract_attachment(b"not a PDF", "receipt.pdf")

    async def test_send_failure_does_not_duplicate_saved_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            store.init()
            sender = types.SimpleNamespace(id=1, first_name="User", username="user")
            msg = types.SimpleNamespace(from_user=sender, chat_id=2, message_id=3)
            progress = types.SimpleNamespace(
                edit_text=AsyncMock(side_effect=RuntimeError("Telegram unavailable"))
            )
            analysis = dict(
                tax_category="Nicht abzugsfähig",
                tax_subcategory="Lebenshaltungskosten",
                tax_deductible=False,
            )
            with patch.object(main, "store", store):
                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        await main._request_clarification_or_save(
                            msg, progress, analysis, {"message_type": "text"}
                        )
            self.assertEqual(len(store.get_all_receipts()), 1)


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def grounded_analyzer(self, metadata, receipt_date):
        evidence = "Aufwendungen für Arbeitsmittel sind Werbungskosten."
        analyzer = make_analyzer(
            dict(
                tax_category="Werbungskosten",
                tax_subcategory="Arbeitsmittel",
                tax_deductible=True,
                deduction_notes="test",
                receipt_date=receipt_date,
                source_ids=["test-source"],
                evidence=[{"source_id": "test-source", "quote": evidence}],
            )
        )
        analyzer.tax_knowledge = types.SimpleNamespace(
            metadata=lambda: metadata,
            search=lambda *_args, **_kwargs: [
                {
                    "id": "test-source",
                    "section": "§ 9",
                    "title": "Test",
                    "text": evidence,
                    "source_url": "https://example.org",
                    "score": 1.0,
                }
            ],
        )
        return analyzer

    async def test_cited_assessment_without_validity_window_is_not_forced_into_review(self):
        for receipt_date in ("2026-09-30", None):
            with self.subTest(receipt_date=receipt_date):
                analyzer = self.grounded_analyzer({}, receipt_date)
                result = await analyzer._call_claude("Monitor for work", False)
                self.assertFalse(result["needs_review"])
                self.assertTrue(result["tax_deductible"])

    async def test_date_outside_declared_validity_window_requires_review(self):
        window = {"valid_from": "2025-01-01", "valid_to": "2025-12-31"}
        for receipt_date, needs_review in (("2026-09-30", True), ("2025-06-01", False)):
            with self.subTest(receipt_date=receipt_date):
                analyzer = self.grounded_analyzer(window, receipt_date)
                result = await analyzer._call_claude("Monitor for work", False)
                self.assertEqual(result["needs_review"], needs_review)
                self.assertEqual(result["tax_deductible"], not needs_review)
                self.assertEqual(
                    [source["id"] for source in result["rag_sources"]], ["test-source"]
                )

    async def test_forged_evidence_excerpt_is_rejected(self):
        analyzer = make_analyzer(
            dict(
                tax_category="Werbungskosten",
                tax_subcategory="Arbeitsmittel",
                tax_deductible=True,
                deduction_notes="test",
                source_ids=["test-source"],
                evidence=[
                    {
                        "source_id": "test-source",
                        "quote": "This is not in the source text.",
                    }
                ],
            )
        )
        result = await analyzer._call_claude("Monitor for work", False)
        self.assertFalse(result["analysis_error"])
        self.assertTrue(result["needs_review"])
        self.assertFalse(result["tax_deductible"])
        self.assertEqual(result["rag_sources"], [])
        self.assertEqual(result["evidence"], [])
        self.assertNotIn("test", result["deduction_notes"])

    async def test_api_deadline_returns_error_without_saving(self):
        analyzer = make_analyzer({})
        analyzer.clarification_enabled = False

        async def hang(*args):
            await asyncio.sleep(10)

        analyzer._call_claude = hang
        import taxedo.agent.analyzer as module

        with patch.object(module.config, "ANALYSIS_TIMEOUT_SECONDS", 0.001):
            result = await analyzer.analyze_text("Expense")
        self.assertTrue(result["analysis_error"])

    async def test_retrieval_failure_does_not_call_model(self):
        analyzer = make_analyzer({})

        def fail(*args, **kwargs):
            raise RuntimeError("unavailable")

        analyzer.tax_knowledge.search = fail
        result = await analyzer._call_claude("Work monitor", False)
        self.assertTrue(result["analysis_error"])
        self.assertIsNone(analyzer.client.messages.system)
