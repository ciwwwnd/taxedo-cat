import re
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import taxedo.bot.app as main
from taxedo.agent.analyzer import _validate_classification
from taxedo.bot.reporting import currency_totals, format_totals
from taxedo.storage.clarifications import ClarificationStore
from taxedo.storage.receipts import ReceiptStore


class BotHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_is_valid_telegram_html(self):
        message = NS(reply_text=AsyncMock())
        await main.cmd_start.__wrapped__(NS(message=message), NS())
        self.assertEqual(message.reply_text.call_args.kwargs["parse_mode"], "HTML")
        tags = set(re.findall(r"</?([a-zA-Z-]+)", message.reply_text.call_args.args[0]))
        self.assertLessEqual(tags, {"b", "i", "u", "s", "a", "code", "pre"})

    async def test_model_question_is_pending_until_answer_then_saved_once(self):
        from taxedo.agent.analyzer import _blank_result
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReceiptStore(root)
            store.init()
            pending = ClarificationStore(root / "receipts.db")
            analysis = _blank_result("Missing purpose.")
            analysis.update(needs_clarification=True, clarification_question="Who was this for?")
            msg = NS(chat_id=1, from_user=NS(id=2, first_name="User"), message_id=10, text="For my household")
            progress = NS(edit_text=AsyncMock())
            final = _blank_result("Personal purchase; review required.")
            final.update(needs_review=True)
            model = NS(analyze_text=AsyncMock(return_value=final))
            with patch.object(main, "store", store), patch.object(main, "clarifications", pending), patch.object(main, "analyzer", model), patch.object(main.config, "AGENTIC_CLARIFICATION", True):
                await main._request_clarification_or_save(msg, progress, analysis, {
                    "message_type": "photo", "file_path": None, "file_hash": None, "raw_text": "Bread 10.44 EUR",
                })
                self.assertEqual(store.get_all_receipts(), [])
                self.assertEqual(pending.get(1, 2).current_question, "Who was this for?")
                msg.message_id = 11
                await main._resume_clarification(msg, progress)
                self.assertIsNone(pending.get(1, 2))
                self.assertEqual(len(store.get_all_receipts()), 1)
                self.assertEqual(model.analyze_text.call_args.kwargs["answers"][0]["answer"], "For my household")

    async def test_confirmation_is_authorized_audited_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            store.init()
            receipt_id = store.insert_receipt(
                needs_review=True,
                tax_deductible=False,
                total_amount=21.42,
                currency="EUR",
                tax_category="Werbungskosten",
                tax_subcategory="Fortbildungskosten",
            )
            query = NS(
                data=f"deductible:{receipt_id}",
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            update = NS(
                callback_query=query, effective_user=NS(id=2), effective_chat=NS(id=1)
            )
            with patch.object(main, "store", store), patch.object(
                main, "is_authorized", return_value=False
            ):
                await main.confirm_deductible(update, NS())
                self.assertEqual(store.review_history(receipt_id), [])
            with patch.object(main, "store", store), patch.object(
                main, "is_authorized", return_value=True
            ):
                await main.confirm_deductible(update, NS())
                await main.confirm_deductible(update, NS())
            self.assertTrue(store.get_receipt_by_id(receipt_id)["tax_deductible"])
            self.assertEqual(len(store.review_history(receipt_id)), 1)
            query.edit_message_text.assert_awaited_once()

    def test_unknown_merchant_is_valid(self):
        result = dict(
            store_name=None,
            tax_category="Werbungskosten",
            tax_subcategory="Arbeitsmittel",
            deduction_notes="Work monitor",
            tax_deductible=True,
        )
        _validate_classification(result)
        self.assertEqual(result["store_name"], "")


class EnglishAndRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_rereads_pending_attachment_without_an_answer(self):
        from taxedo.agent.analyzer import _blank_result
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "files").mkdir()
            (root / "files" / "receipt.jpg").write_bytes(b"original image")
            store = ReceiptStore(root)
            store.init()
            pending = ClarificationStore(root / "receipts.db")
            pending.start(1, 2, "bad OCR", "Send the receipt again", {
                "message_type": "photo", "file_path": "files/receipt.jpg",
                "file_hash": "hash", "message_id": 10, "chat_id": 1, "caption": "",
            })
            final = _blank_result("Review required.")
            final.update(needs_review=True, _source_text="Bread 10.44 EUR", total_amount=10.44)
            analyzer = NS(analyze_document=AsyncMock(return_value=final))
            progress = NS(edit_text=AsyncMock())
            msg = NS(chat_id=1, from_user=NS(id=2, first_name="User"), message_id=12,
                     reply_text=AsyncMock(return_value=progress))
            with patch.object(main, "store", store), patch.object(main, "clarifications", pending), patch.object(main, "analyzer", analyzer), patch.object(main.config, "DATA_DIR", root):
                await main.cmd_retry.__wrapped__(NS(message=msg), NS())
            analyzer.analyze_document.assert_awaited_once_with(b"original image", "receipt.jpg", "")
            self.assertIsNone(pending.get(1, 2))
            self.assertEqual(len(store.get_all_receipts()), 1)
            self.assertEqual(store.find_by_message(1, 10)["total_amount"], 10.44)

    async def test_retry_keeps_original_answer_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReceiptStore(root)
            store.init()
            pending = ClarificationStore(root / "receipts.db")
            pending.start(
                1,
                2,
                "Monitor 689 EUR",
                "Purpose?",
                {"message_type": "photo", "chat_id": 1, "message_id": 10},
            )
            pending.record_answer(1, 2, "I use it both for my work and other", 12)
            progress = NS(edit_text=AsyncMock())
            msg = NS(
                chat_id=1,
                from_user=NS(id=2, first_name="User"),
                message_id=14,
                text="/retry",
                reply_text=AsyncMock(return_value=progress),
            )
            analyzer = NS(
                analyze_text=AsyncMock(
                    side_effect=[
                        {
                            "analysis_error": True,
                            "deduction_notes": "Validation failed.",
                        },
                        {
                            "tax_category": "Werbungskosten",
                            "tax_subcategory": "Arbeitsmittel",
                            "needs_review": True,
                        },
                    ]
                )
            )
            with patch.object(main, "store", store), patch.object(
                main, "clarifications", pending
            ), patch.object(main, "analyzer", analyzer):
                await main.cmd_retry.__wrapped__(NS(message=msg), NS())
                self.assertIn("/retry", progress.edit_text.call_args.args[0])
                self.assertEqual(len(pending.get(1, 2).answers), 1)
                msg.message_id = 15
                await main.cmd_retry.__wrapped__(NS(message=msg), NS())
            answers = analyzer.analyze_text.call_args.kwargs["answers"]
            self.assertEqual(len(answers), 1)
            self.assertEqual(
                answers[0]["answer"], "I use it both for my work and other"
            )
            self.assertEqual(len(store.get_all_receipts()), 1)
            self.assertIsNone(pending.get(1, 2))


class DeductionExplanationTests(unittest.TestCase):
    def test_pending_work_expense_is_not_presented_as_rejected(self):
        text = main.deduction_explanation(
            dict(needs_review=True, tax_category="Werbungskosten", rag_sources=[])
        )
        self.assertIn("has not been established", text)

    def test_supported_reason_and_review_reason_are_preserved_and_escaped(self):
        receipt = dict(tax_deductible=False, deduction_notes="Private use <only>")
        self.assertIn(
            "Private use &lt;only&gt;",
            main.format_analysis_response(receipt, 4),
        )
        receipt.update(
            review_status="rejected", review_reason="Reviewer checked private use"
        )
        self.assertIn(
            "Reviewer checked private use",
            main.format_analysis_response(receipt, 4),
        )


class CancelReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_id_is_audited_and_does_not_clear_clarification(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            store.init()
            receipt_id = store.insert_receipt(
                needs_review=True, tax_deductible=False, total_amount=699
            )
            pending = ClarificationStore(Path(directory) / "receipts.db")
            pending.start(1, 2, "Other expense", "Purpose?", {})
            message = NS(reply_text=AsyncMock())
            update = NS(
                effective_user=NS(id=2), effective_chat=NS(id=1), message=message
            )
            with patch.object(main, "store", store), patch.object(
                main, "clarifications", pending
            ), patch.object(main, "is_authorized", return_value=True):
                for args in ([str(receipt_id)], [str(receipt_id)], ["bad"], ["9999"]):
                    await main.cmd_cancel.__wrapped__(update, NS(args=args))
                self.assertIsNotNone(pending.get(1, 2))
                self.assertEqual(store.pending_reviews(), [])
                receipt = store.get_receipt_by_id(receipt_id)
                self.assertEqual(receipt["review_status"], "cancelled")
                self.assertFalse(receipt["tax_deductible"])
                self.assertEqual(len(store.review_history(receipt_id)), 1)
                await main.cmd_cancel.__wrapped__(update, NS(args=[]))
                self.assertIsNone(pending.get(1, 2))

    async def test_unauthorized_user_cannot_cancel_saved_review(self):
        from unittest.mock import Mock

        store = Mock()
        update = NS(
            effective_user=NS(id=3),
            effective_chat=NS(id=1),
            message=NS(reply_text=AsyncMock()),
        )
        with patch.object(main, "store", store), patch.object(
            main, "is_authorized", return_value=False
        ):
            await main.cmd_cancel(update, NS(args=["4"]))
        store.review_receipt.assert_not_called()

    def test_pending_receipts_always_offer_manual_review(self):
        for category in ("Unklassifiziert", "Nicht abzugsfähig", "unknown"):
            keyboard = main.review_keyboard(
                dict(needs_review=True, tax_category=category), 4
            )
            self.assertEqual(
                [button.callback_data for row in keyboard.inline_keyboard for button in row],
                ["edit:4", "manual_review:4"],
            )
        self.assertEqual(main.review_keyboard(dict(needs_review=False), 4).inline_keyboard[0][0].callback_data, "edit:4")

    async def test_manual_review_guidance_and_invalid_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            store.init()
            receipt_id = store.insert_receipt(
                needs_review=True, tax_deductible=False, tax_category="Unklassifiziert"
            )
            query = NS(
                data=f"manual_review:{receipt_id}", answer=AsyncMock(),
                message=NS(reply_text=AsyncMock()), edit_message_text=AsyncMock(),
            )
            update = NS(callback_query=query, effective_user=NS(id=2), effective_chat=NS(id=1))
            with patch.object(main, "store", store), patch.object(main, "is_authorized", return_value=False):
                await main.manual_review(update, NS())
                query.message.reply_text.assert_not_awaited()
            with patch.object(main, "store", store), patch.object(main, "is_authorized", return_value=True):
                await main.manual_review(update, NS())
                text = query.message.reply_text.call_args.args[0]
                self.assertIn(f"/correct {receipt_id}", text)
                self.assertIn("Work equipment", text)
                self.assertNotIn("Werbungskosten", text)
                self.assertNotIn("Lebenshaltungskosten", text)
                self.assertLess(len(text), 4096)
                query.data = f"deductible:{receipt_id}"
                await main.confirm_deductible(update, NS())
                self.assertIn("valid category", query.answer.call_args.args[0])
                self.assertEqual(store.review_history(receipt_id), [])
                query.edit_message_text.assert_not_awaited()
                store.review_receipt(receipt_id, 2, "rejected", "Personal purchase", deductible=False)
                query.data = f"manual_review:{receipt_id}"
                await main.manual_review(update, NS())
                query.message.reply_text.assert_awaited_once()
                query.data = "manual_review:99999"
                await main.manual_review(update, NS())
                self.assertEqual(query.answer.call_args.args[0], "Receipt not found.")


class EnglishCategoryTests(unittest.IsolatedAsyncioTestCase):
    def test_all_category_labels_are_translated_and_resolve(self):
        from taxedo.tax.categories import TAX_CATEGORIES, english_label, canonical_categories
        for category, details in TAX_CATEGORIES.items():
            self.assertTrue(details["label"])
            for subcategory, subcategory_details in details["subcategories"].items():
                self.assertTrue(subcategory_details["label"])
                self.assertEqual(
                    canonical_categories(english_label(category).lower(), english_label(subcategory).lower()),
                    (category, subcategory),
                )
                self.assertEqual(canonical_categories(category, subcategory), (category, subcategory))

    def test_categories_message_is_english_and_fits_one_telegram_message(self):
        from taxedo.tax.categories import get_tax_info_message
        text = get_tax_info_message()
        self.assertIn("Employment expenses</b> <i>(Werbungskosten)</i>", text)
        self.assertIn("Work equipment", text)
        self.assertLessEqual(len(text), 4096)

    async def test_english_correction_is_saved_with_canonical_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            store.init()
            receipt_id = store.insert_receipt(needs_review=True, tax_category="Unklassifiziert")
            update = NS(effective_user=NS(id=2), message=NS(
                text=f"/correct {receipt_id} Not deductible | Personal living expenses | no | Private groceries",
                reply_text=AsyncMock(),
            ))
            with patch.object(main, "store", store):
                await main.cmd_review_decision.__wrapped__(update, NS())
            receipt = store.get_receipt_by_id(receipt_id)
            self.assertEqual(receipt["tax_category"], "Nicht abzugsfähig")
            self.assertEqual(receipt["tax_subcategory"], "Lebenshaltungskosten")
            self.assertFalse(receipt["needs_review"])
            self.assertFalse(receipt["tax_deductible"])


class AttachmentDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_metadata_does_not_reject_download(self):
        file = NS(file_size=None, download_as_bytearray=AsyncMock(return_value=bytearray(b"photo")))
        bot = NS(get_file=AsyncMock(return_value=file))
        self.assertEqual(await main.download_attachment(NS(file_id="1", file_size=None), bot), b"photo")

    async def test_telegram_size_limit_is_explained(self):
        from telegram.error import BadRequest
        bot = NS(get_file=AsyncMock(side_effect=BadRequest("File is too big")))
        with self.assertRaisesRegex(ValueError, r"Cannot download this file \(20 MB limit\)"):
            await main.download_attachment(NS(file_id="1", file_size=30 * 1024**2), bot)

    async def test_rejected_attachment_reason_reaches_the_user(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            store.init()
            msg = NS(chat_id=1, message_id=10, from_user=NS(id=2), text=None, caption=None,
                     document=NS(file_name="statement.zip"), reply_text=AsyncMock())
            update = NS(message=msg, effective_user=msg.from_user, effective_chat=NS(id=1))
            with patch.object(main, "store", store), patch.object(
                main, "clarifications", ClarificationStore(store.db_path)
            ), patch.object(main, "is_authorized", return_value=True):
                await main.handle_document(update, NS(bot=NS()))
        reply = msg.reply_text.await_args.args[0]
        self.assertIn("Unsupported document format: .zip", reply)
        self.assertIn(".pdf", reply)


class ProfileAndReviewGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_myid_escapes_profile_names(self):
        message = NS(reply_text=AsyncMock())
        user = NS(id=5, first_name="Tom & <Jerry>", last_name=None, username=None)
        await main.cmd_myid(NS(effective_user=user, message=message), NS())
        self.assertIn("Tom &amp; &lt;Jerry&gt;", message.reply_text.await_args.args[0])

    def test_only_deductible_categories_can_be_confirmed_deductible(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            store.init()
            receipt_id = store.insert_receipt(
                needs_review=True, tax_category="Unklassifiziert", tax_subcategory=""
            )
            for action, category, subcategory in (
                ("approved", None, None),
                ("corrected", "Nicht abzugsfähig", "Lebenshaltungskosten"),
            ):
                with self.subTest(action=action), self.assertRaisesRegex(
                    ValueError, "deductible category"
                ):
                    store.review_receipt(receipt_id, 2, action, "reason", deductible=True,
                                         category=category, subcategory=subcategory)
            self.assertEqual(store.review_history(receipt_id), [])
            reviewed = store.review_receipt(receipt_id, 2, "approved", "private", deductible=False)
            self.assertFalse(reviewed["tax_deductible"])


class ClarificationReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_older_answer_replay_preserves_unanswered_question_after_restart(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.db"
            pending = ClarificationStore(path)
            pending.start(1, 2, "expense", "Q1", {"message_id": 10})
            pending.record_answer(1, 2, "first answer", 11)
            pending.set_next_question(1, 2, "Q2")
            reopened = ClarificationStore(path)
            analyzer = NS(
                analyze_text=AsyncMock(
                    return_value={
                        "analysis_error": True,
                        "deduction_notes": "Service unavailable",
                    }
                )
            )
            msg = NS(chat_id=1, from_user=NS(id=2), message_id=11, text="first answer")
            progress = NS(edit_text=AsyncMock())
            with patch.object(main, "clarifications", reopened), patch.object(
                main, "analyzer", analyzer
            ):
                await main._resume_clarification(msg, progress)
                analyzer.analyze_text.assert_not_awaited()
                self.assertIn("Q2", progress.edit_text.call_args.args[0])
                self.assertEqual(len(reopened.get(1, 2).answers), 1)

                msg.message_id, msg.text = 12, "second answer"
                await main._resume_clarification(msg, progress)
                await main._resume_clarification(msg, progress)
                self.assertEqual(analyzer.analyze_text.await_count, 2)
                self.assertEqual(len(reopened.get(1, 2).answers), 2)


class CurrencyReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_interactive_and_scheduled_reports_keep_currencies_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptStore(Path(directory))
            store.init()
            for currency in ("EUR", "USD"):
                store.insert_receipt(
                    total_amount=100,
                    currency=currency,
                    tax_deductible=True,
                    sender_name="User",
                    tax_category="Work",
                    store_name="Shop",
                )
            self.assertEqual(
                store.get_stats()["totals_by_currency"], {"EUR": 100, "USD": 100}
            )
            self.assertEqual(
                store.get_stats_for_year(datetime.now().year)["deductible_by_currency"],
                {"EUR": 100, "USD": 100},
            )
            self.assertEqual(
                store.get_stats()["top_categories"],
                [("Work", "EUR", 100), ("Work", "USD", 100)],
            )
            message = NS(reply_text=AsyncMock())
            update = NS(message=message)
            bot = NS(send_message=AsyncMock(), send_document=AsyncMock())
            ctx = NS(args=[], bot=bot)
            with patch.object(main, "store", store), patch.object(
                main.config, "GROUP_CHAT_ID", 1
            ):
                await main.cmd_summary.__wrapped__(update, ctx)
                await main.cmd_stats.__wrapped__(update, ctx)
                await main.weekly_csv_report(ctx)
                await main.weekly_deduction_reminder(ctx)
                for call in message.reply_text.call_args_list:
                    self.assert_currency_text(call.args[0])
                self.assert_currency_text(bot.send_document.call_args.kwargs["caption"])
                self.assert_currency_text(bot.send_message.call_args.kwargs["text"])
                with patch.object(
                    store,
                    "get_stats_for_year",
                    return_value=store.get_stats_for_year(datetime.now().year),
                ):
                    await main.march_tax_reminder(ctx)
                self.assert_currency_text(bot.send_message.call_args.kwargs["text"])

    def assert_currency_text(self, text):
        self.assertIn("€100.00", text)
        self.assertIn("USD 100.00", text)
        self.assertNotIn("€200.00", text)

    def test_legacy_currency_decimal_sums_and_item_labels(self):
        totals = currency_totals(
            [{"total_amount": 0.1}, {"total_amount": 0.2, "currency": ""}]
        )
        self.assertEqual(totals, {"EUR": Decimal("0.3")})
        self.assertEqual(format_totals(totals), "€0.30")
        response = main.format_analysis_response({
                "currency": "USD",
                "total_amount": 100,
                "items": [{"name": "Item", "price": 100}],
            }, 1)
        self.assertEqual(response.count("USD 100.00"), 2)
        self.assertNotIn("€", response)
