from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from taxedo.security import receipt_path

PENDING_CLARIFICATIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS pending_clarifications (
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        original_text TEXT NOT NULL,
        answers_json TEXT NOT NULL,
        current_question TEXT NOT NULL,
        question_count INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, user_id)
    )
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PendingClarification:
    chat_id: int
    user_id: int
    original_text: str
    answers: tuple[dict[str, Any], ...]
    current_question: str
    question_count: int
    payload: dict[str, Any]
    created_at: datetime
    expires_at: datetime

    def is_previous_answer(self, message_id: int) -> bool:
        for answer in self.answers:
            if answer.get("message_id") == message_id:
                if "question_count" in answer:
                    return answer["question_count"] < self.question_count
                return answer["question"] != self.current_question
        return False

    def combined_text(self) -> str:
        parts = [self.original_text, "", "--- CLARIFICATION DIALOGUE ---"]
        for item in self.answers:
            parts.append(f"Question: {item['question']}")
            parts.append(f"User answer: {item['answer']}")
        return "\n".join(parts)


class ClarificationStore:
    def __init__(
        self,
        path: Path,
        *,
        ttl_minutes: int = 30,
        max_questions: int = 2,
        clock: Callable[[], datetime] = _utcnow,
    ):
        if ttl_minutes < 1:
            raise ValueError("ttl_minutes must be at least 1")
        if max_questions < 1:
            raise ValueError("max_questions must be at least 1")
        self.path = path
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_questions = max_questions
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(PENDING_CLARIFICATIONS_TABLE)

    def start(
        self,
        chat_id: int,
        user_id: int,
        original_text: str,
        question: str,
        payload: dict[str, Any],
    ) -> PendingClarification:
        now = self.clock()
        expires_at = now + self.ttl
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT payload_json FROM pending_clarifications WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            ).fetchall()
            connection.execute(
                """
                INSERT INTO pending_clarifications (
                    chat_id, user_id, original_text, answers_json,
                    current_question, question_count, payload_json,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    original_text=excluded.original_text,
                    answers_json=excluded.answers_json,
                    current_question=excluded.current_question,
                    question_count=excluded.question_count,
                    payload_json=excluded.payload_json,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (
                    chat_id,
                    user_id,
                    original_text,
                    "[]",
                    question,
                    1,
                    json.dumps(payload, ensure_ascii=False),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            self._remove_unreferenced_files(connection, [row[0] for row in previous])
        state = self.get(chat_id, user_id)
        if state is None:
            raise RuntimeError("Failed to persist clarification state")
        return state

    def get(self, chat_id: int, user_id: int) -> PendingClarification | None:
        now = self.clock()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM pending_clarifications
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= now:
                connection.execute(
                    "DELETE FROM pending_clarifications "
                    "WHERE chat_id = ? AND user_id = ?",
                    (chat_id, user_id),
                )
                self._remove_unreferenced_files(connection, [row["payload_json"]])
                return None
        return self._from_row(row)

    def record_answer(
        self, chat_id: int, user_id: int, answer: str, message_id: int | None = None
    ) -> PendingClarification | None:
        state = self.get(chat_id, user_id)
        if state is None:
            return None
        if message_id is not None and any(
            item.get("message_id") == message_id for item in state.answers
        ):
            return state
        answers = [
            *state.answers,
            {
                "question": state.current_question,
                "answer": answer.strip(),
                "message_id": message_id,
                "question_count": state.question_count,
            },
        ]
        expires_at = self.clock() + self.ttl
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pending_clarifications
                SET answers_json = ?, expires_at = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (
                    json.dumps(answers, ensure_ascii=False),
                    expires_at.isoformat(),
                    chat_id,
                    user_id,
                ),
            )
        return self.get(chat_id, user_id)

    def set_next_question(
        self, chat_id: int, user_id: int, question: str
    ) -> PendingClarification:
        state = self.get(chat_id, user_id)
        if state is None:
            raise KeyError("No active clarification for this user and chat")
        if state.question_count >= self.max_questions:
            raise ValueError("Clarification question limit reached")
        expires_at = self.clock() + self.ttl
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pending_clarifications
                SET current_question = ?, question_count = ?, expires_at = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (
                    question,
                    state.question_count + 1,
                    expires_at.isoformat(),
                    chat_id,
                    user_id,
                ),
            )
        updated = self.get(chat_id, user_id)
        if updated is None:
            raise RuntimeError("Clarification state disappeared during update")
        return updated

    def clear(self, chat_id: int, user_id: int) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT payload_json FROM pending_clarifications WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            ).fetchall()
            cursor = connection.execute(
                "DELETE FROM pending_clarifications WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            self._remove_unreferenced_files(connection, [row[0] for row in rows])
        return cursor.rowcount > 0

    def purge_expired(self) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cutoff = self.clock().isoformat()
            rows = connection.execute(
                "SELECT payload_json FROM pending_clarifications WHERE expires_at <= ?",
                (cutoff,),
            ).fetchall()
            cursor = connection.execute(
                "DELETE FROM pending_clarifications WHERE expires_at <= ?",
                (cutoff,),
            )
            self._remove_unreferenced_files(connection, [row[0] for row in rows])
        return cursor.rowcount

    def _remove_unreferenced_files(self, connection, payloads):
        """Delete abandoned attachments while holding the database write lock."""
        def paths(values):
            result = set()
            for value in values:
                stored = json.loads(value).get("file_path")
                if stored:
                    try:
                        result.add(receipt_path(self.path.parent, stored))
                    except ValueError:
                        continue
            return result

        candidates = paths(payloads)
        if not candidates:
            return
        retained = paths(row[0] for row in connection.execute(
            "SELECT payload_json FROM pending_clarifications"
        ))
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='receipts'"
        ).fetchone():
            retained.update(paths(row[0] for row in connection.execute("SELECT payload FROM receipts")))
        for path in candidates - retained:
            path.unlink(missing_ok=True)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PendingClarification:
        return PendingClarification(
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            original_text=row["original_text"],
            answers=tuple(json.loads(row["answers_json"])),
            current_question=row["current_question"],
            question_count=row["question_count"],
            payload=json.loads(row["payload_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )
