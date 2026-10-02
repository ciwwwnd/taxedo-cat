import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from taxedo.agent.analyzer import _blank_result
from taxedo.storage.receipts import ReceiptStore
from taxedo.storage.clarifications import ClarificationStore
import taxedo.bot.app as app


class EditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = ReceiptStore(root)
        self.store.init()
        self.pending = ClarificationStore(root / "receipts.db")
        self.receipt_id = self.store.insert_receipt(
            raw_text="Chair for home office. I work hybrid two days per week.",
            total_amount=1500, currency="EUR", file_path="files/chair.jpg", file_hash="hash",
            tax_category="Werbungskosten", tax_subcategory="Arbeitsmittel",
            needs_review=True, tax_deductible=False, sender_name="User",
        )
        self.store.review_receipt(self.receipt_id, 2, "approved", "Checked", deductible=True)
        self.store.link_message(self.receipt_id, 1, 50)
        self.progress = NS(chat_id=1, message_id=60, edit_text=AsyncMock())
        self.msg = NS(chat_id=1, message_id=55, from_user=NS(id=2, first_name="User"),
                      text="Actually the chair cost 1400 and I use it only for work.",
                      reply_to_message=NS(message_id=50),
                      reply_text=AsyncMock(return_value=self.progress))
        self.result = _blank_result("You use the chair only for work. Work equipment is proposed; the purchase date remains unknown.")
        self.result.update(total_amount=1400, tax_category="Werbungskosten", tax_subcategory="Arbeitsmittel", needs_review=True)
        self.analyzer = NS(analyze_text=AsyncMock(return_value=self.result))

    async def test_reply_edits_same_receipt_and_reopens_review(self):
        with patch.object(app, "store", self.store), patch.object(app, "clarifications", self.pending), patch.object(app, "analyzer", self.analyzer):
            await app.handle_text.__wrapped__(NS(message=self.msg), NS())
        updated = self.store.get_receipt_by_id(self.receipt_id)
        self.assertEqual(len(self.store.get_all_receipts()), 1)
        self.assertEqual(updated["total_amount"], 1400)
        self.assertEqual(updated["file_path"], "files/chair.jpg")
        self.assertEqual(updated["file_hash"], "hash")
        self.assertTrue(updated["needs_review"])
        self.assertFalse(updated["tax_deductible"])
        self.assertEqual(updated["review_status"], "pending")
        sent = self.analyzer.analyze_text.call_args.args[0]
        self.assertIn("hybrid two days", sent)
        self.assertIn("1400", sent)
        self.assertEqual(self.store.find_by_message(1, 60)["id"], self.receipt_id)
        self.assertEqual(self.store.find_by_message(1, 55)["id"], self.receipt_id)
        self.assertEqual(self.store.review_history(self.receipt_id)[-1]["action"], "edited")
        response = self.progress.edit_text.call_args.args[0]
        self.assertIn("only for work", response)
        self.assertIn("Work equipment", response)

    async def test_command_updates_and_failed_analysis_preserves_receipt(self):
        before = self.store.get_receipt_by_id(self.receipt_id)
        self.msg.text = f"/edit {self.receipt_id} The amount is 1400"
        self.analyzer.analyze_text.return_value = {"analysis_error": True, "deduction_notes": "Unavailable"}
        with patch.object(app, "store", self.store), patch.object(app, "clarifications", self.pending), patch.object(app, "analyzer", self.analyzer):
            await app.cmd_edit.__wrapped__(NS(message=self.msg), NS())
        self.assertEqual(before, self.store.get_receipt_by_id(self.receipt_id))
        self.assertIn("has not changed", self.progress.edit_text.call_args.args[0])

    def test_stale_assessment_cannot_overwrite_new_changes(self):
        before = self.store.get_receipt_by_id(self.receipt_id)
        self.store.edit_analysis(self.receipt_id, 2, self.result, "First update", before)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.store.edit_analysis(self.receipt_id, 3, self.result, "Stale update", before)
        self.assertEqual(self.store.get_receipt_by_id(self.receipt_id)["raw_text"], "First update")

    async def test_button_links_persistent_reply_prompt_and_checks_auth(self):
        query = NS(data=f"edit:{self.receipt_id}", answer=AsyncMock(), message=self.msg)
        update = NS(callback_query=query, effective_user=NS(id=2), effective_chat=NS(id=1))
        with patch.object(app, "store", self.store), patch.object(app, "clarifications", self.pending), patch.object(app, "is_authorized", return_value=False):
            await app.prompt_edit(update, NS())
            self.msg.reply_text.assert_not_awaited()
        with patch.object(app, "store", self.store), patch.object(app, "clarifications", self.pending), patch.object(app, "is_authorized", return_value=True):
            await app.prompt_edit(update, NS())
        self.assertEqual(self.store.find_by_message(1, 60)["id"], self.receipt_id)
        self.assertTrue(self.msg.reply_text.call_args.kwargs["reply_markup"].force_reply)
