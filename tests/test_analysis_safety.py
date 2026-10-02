from pathlib import Path
import types
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import taxedo.bot.app as app
from taxedo.agent.analyzer import _validate_classification
from taxedo.tax.knowledge import _normalize_vectors
from taxedo.storage.clarifications import ClarificationStore


class SafetyTests(unittest.TestCase):
    def test_failed_or_pending_analysis_cannot_be_saved(self):
        store = Mock()
        with patch.object(app, "store", store):
            for analysis in ({"analysis_error": True}, {"needs_clarification": True}):
                with self.assertRaises(ValueError):
                    app._save_final_receipt(None, analysis, {})
        store.insert_receipt.assert_not_called()

    def test_invalid_embeddings_fail(self):
        for vectors in ([[0, 0]], [[float("nan"), 1]], [[float("inf"), 1]], []):
            with self.assertRaises(ValueError):
                _normalize_vectors(np.array(vectors))

    def test_nonfinite_amounts_fail(self):
        for value in ("25", True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                _validate_classification(
                    {
                        "tax_category": "Werbungskosten",
                        "tax_subcategory": "Arbeitsmittel",
                        "deduction_notes": "Test",
                        "tax_deductible": True,
                        "total_amount": value,
                    }
                )


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_resume_keeps_pending_state(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "receipts.db"
            clarifications = ClarificationStore(database)
            clarifications.start(1, 2, "Expense", "What was its purpose?", {})
            analyzer = types.SimpleNamespace(
                analyze_text=AsyncMock(return_value={
                    "analysis_error": True,
                    "deduction_notes": "Retrieval failed",
                })
            )
            save = Mock()
            message = types.SimpleNamespace(
                chat_id=1, message_id=2,
                from_user=types.SimpleNamespace(id=2), text="For work",
            )
            progress = types.SimpleNamespace(edit_text=AsyncMock())
            with patch.object(app, "clarifications", clarifications), patch.object(
                app, "analyzer", analyzer
            ), patch.object(app, "_save_final_receipt", save):
                await app._resume_clarification(message, progress)

            pending = ClarificationStore(database).get(1, 2)
            self.assertIsNotNone(pending)
            self.assertEqual(pending.original_text, "Expense")
            self.assertEqual(len(pending.answers), 1)
            self.assertEqual(pending.answers[0]["answer"], "For work")
            analyzer.analyze_text.assert_awaited_once_with(
                "Expense", answers=pending.answers, allow_clarification=True,
            )
            save.assert_not_called()
            self.assertIn("/retry", progress.edit_text.call_args.args[0])
