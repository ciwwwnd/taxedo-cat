from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from taxedo.storage.clarifications import ClarificationStore


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


class ClarificationStoreTests(unittest.TestCase):
    def test_state_is_persistent_and_isolated_by_chat_and_user(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "clarifications.db"
            clock = MutableClock()
            store = ClarificationStore(path, clock=clock)
            store.start(10, 20, "Course fee", "What was its purpose?", {"kind": "text"})

            reopened = ClarificationStore(path, clock=clock)

            self.assertEqual(reopened.get(10, 20).original_text, "Course fee")
            self.assertIsNone(reopened.get(10, 21))
            self.assertIsNone(reopened.get(11, 20))

    def test_answer_is_merged_and_question_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ClarificationStore(
                Path(temporary_directory) / "clarifications.db",
                max_questions=2,
            )
            store.start(1, 2, "Monitor", "Any private use?", {"kind": "text"})
            answered = store.record_answer(1, 2, "No, work only")
            state = store.set_next_question(1, 2, "Any reimbursement?")

            self.assertIn("User answer: No, work only", answered.combined_text())
            self.assertEqual(state.question_count, 2)
            with self.assertRaises(ValueError):
                store.set_next_question(1, 2, "One more?")

    def test_expired_state_is_deleted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            clock = MutableClock()
            store = ClarificationStore(
                Path(temporary_directory) / "clarifications.db",
                ttl_minutes=5,
                clock=clock,
            )
            store.start(1, 2, "Expense", "What use?", {})
            clock.value += timedelta(minutes=6)

            self.assertIsNone(store.get(1, 2))
            self.assertFalse(store.clear(1, 2))

    def test_clear_only_removes_exact_owner(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ClarificationStore(Path(temporary_directory) / "state.db")
            store.start(1, 2, "A", "Question A", {})
            store.start(1, 3, "B", "Question B", {})

            self.assertTrue(store.clear(1, 2))
            self.assertIsNone(store.get(1, 2))
            self.assertIsNotNone(store.get(1, 3))


if __name__ == "__main__":
    unittest.main()
