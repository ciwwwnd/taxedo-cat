import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import taxedo.bot.app as app
from taxedo.storage.receipts import ReceiptStore
from taxedo.storage.clarifications import ClarificationStore
from test_clarification_store import MutableClock


class PersistenceFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ReceiptStore(self.root)
        self.store.init()
        self.clock = MutableClock()
        self.pending = ClarificationStore(self.store.db_path, clock=self.clock)
        (self.root / 'files').mkdir()
        self.file = self.root / 'files' / 'receipt.jpg'
        self.file.write_bytes(b'attachment')

    def start(self, user=2):
        self.pending.start(1, user, 'Receipt', 'Purpose?', {'file_path': 'files/receipt.jpg'})

    def test_year_selection_and_annual_totals_use_receipt_year(self):
        base = dict(created_at='2026-01-02 12:00:00', total_amount=10, currency='EUR', tax_deductible=True)
        first = self.store.insert_receipt(**base, tax_year=2025, receipt_date='2025-12-30')
        second = self.store.insert_receipt(**base, receipt_date='2025-12-31')
        legacy = self.store.insert_receipt(**base)
        self.assertEqual([r['id'] for r in self.store.get_receipts_by_year(2025)], [first, second])
        self.assertEqual([r['id'] for r in self.store.get_receipts_by_year(2026)], [legacy])
        self.assertEqual(self.store.get_stats_for_year(2025)['deductible_by_currency']['EUR'], 20)

    def test_cancel_and_both_expiration_paths_remove_files(self):
        for operation in ('clear', 'get', 'purge_expired'):
            with self.subTest(operation=operation):
                self.file.write_bytes(b'attachment')
                self.start()
                if operation != 'clear':
                    self.clock.value += timedelta(hours=1)
                if operation == 'purge_expired':
                    self.assertEqual(self.pending.purge_expired(), 1)
                else:
                    getattr(self.pending, operation)(1, 2)
                self.assertFalse(self.file.exists())

    def test_cleanup_preserves_other_pending_and_saved_references(self):
        self.start(2)
        self.start(3)
        self.pending.clear(1, 2)
        self.assertTrue(self.file.exists())
        self.store.insert_receipt(file_path=str(self.file))
        self.pending.clear(1, 3)
        self.assertTrue(self.file.exists())

    def test_cleanup_does_not_delete_outside_archive(self):
        outside = self.root / 'private.txt'
        outside.write_text('private')
        self.pending.start(1, 2, 'Receipt', 'Purpose?', {'file_path': 'files/../private.txt'})
        self.pending.clear(1, 2)
        self.assertTrue(outside.exists())


class RetryFixTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_failure_survives_restart_then_retries_once(self):
        for kind in ('text', 'photo', 'document'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = ReceiptStore(root)
                store.init()
                pending = ClarificationStore(store.db_path)
                failure = {'analysis_error': True, 'deduction_notes': 'Service unavailable'}
                success = {'total_amount': 10, 'currency': 'EUR', 'needs_review': True}
                analyzer = NS(analyze_text=AsyncMock(return_value=failure),
                              analyze_image=AsyncMock(return_value=failure),
                              analyze_document=AsyncMock(return_value=failure))
                progress = NS(message_id=11, chat_id=1, edit_text=AsyncMock())
                msg = NS(chat_id=1, message_id=10, from_user=NS(id=2, first_name='User'),
                         text='Work expense 10 EUR', caption='', reply_to_message=None,
                         photo=[NS()], document=NS(file_name='receipt.pdf'),
                         reply_text=AsyncMock(return_value=progress))
                with patch.object(app, 'store', store), patch.object(app, 'clarifications', pending), \
                     patch.object(app, 'analyzer', analyzer), patch.object(app.config, 'DATA_DIR', root), \
                     patch.object(app, 'download_attachment', AsyncMock(return_value=b'original')):
                    await getattr(app, 'handle_' + kind).__wrapped__(NS(message=msg), NS(bot=NS()))
                    self.assertEqual(store.get_all_receipts(), [])
                    self.assertIn('/retry', progress.edit_text.call_args.args[0])
                    reopened = ClarificationStore(store.db_path)
                    state = reopened.get(1, 2)
                    self.assertTrue(state.payload['retry_only'])
                    if kind != 'text':
                        self.assertEqual((root / state.payload['file_path']).read_bytes(), b'original')
                    # A second failed attempt must preserve the original message mapping.
                    msg.message_id = 12
                    with patch.object(app, 'clarifications', reopened):
                        await app.cmd_retry.__wrapped__(NS(message=msg), NS())
                        self.assertEqual(reopened.get(1, 2).payload['message_id'], 10)
                        analyzer.analyze_text.return_value = success
                        analyzer.analyze_document.return_value = success
                        msg.message_id = 13
                        await app.cmd_retry.__wrapped__(NS(message=msg), NS())
                    self.assertIsNone(reopened.get(1, 2))
                    self.assertEqual(len(store.get_all_receipts()), 1)
                    for message_id in (10, 12, 13):
                        self.assertEqual(store.find_by_message(1, message_id)['total_amount'], 10)
                    if kind != 'text':
                        self.assertTrue((root / state.payload['file_path']).exists())
