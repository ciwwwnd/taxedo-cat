from __future__ import annotations

import json
import types

from taxedo.agent.analyzer import (
    ONE_SHOT_PROMPT,
    QUESTION_REVIEW_PROMPT,
    ReceiptAnalyzer,
)
from taxedo.agent.credits import CreditNotifier


class FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.system = None

    async def create(self, **kwargs):
        if kwargs["system"] == QUESTION_REVIEW_PROMPT:
            return types.SimpleNamespace(content=[types.SimpleNamespace(text=json.dumps({
                "question": getattr(self, "reviewed_question", self.payload.get("clarification_question"))
            }))])
        self.system = kwargs["system"]
        if ONE_SHOT_PROMPT in self.system and hasattr(self, "final_payload"):
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=json.dumps(self.final_payload))])
        return types.SimpleNamespace(
            content=[
                types.SimpleNamespace(
                    text=json.dumps(
                        dict(
                            self.payload,
                            source_ids=self.payload.get("source_ids", ["test-source"]),
                        )
                    )
                )
            ],
        )


def make_analyzer(payload):
    analyzer = ReceiptAnalyzer.__new__(ReceiptAnalyzer)
    analyzer.client = types.SimpleNamespace(messages=FakeMessages(payload))
    analyzer.model = "test-model"
    analyzer.temperature = 0
    analyzer.tax_knowledge = types.SimpleNamespace(
        metadata=lambda: {"content_sha256": "test-version"},
        search=lambda *_args, **_kwargs: [
            {
                "id": "test-source",
                "section": "§ 9",
                "title": "Test",
                "text": "Test evidence",
                "source_url": "https://example.org",
                "score": 1.0,
            }
        ],
    )
    analyzer._alert_callback = None
    analyzer.credits = CreditNotifier()
    return analyzer
