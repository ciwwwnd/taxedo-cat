from __future__ import annotations

import types
import unittest

from taxedo.agent.analyzer import (
    CLARIFICATION_POLICY_VERSION,
    CLARIFICATION_PROMPT,
    _parse_json_object,
)
from fakes import make_analyzer


class AnalyzerGroundingTests(unittest.TestCase):
    def test_parser_accepts_complete_json_followed_by_extra_text(self):
        response = '{"tax_deductible": true}\nThis is the result.'

        parsed = _parse_json_object(response)

        self.assertTrue(parsed["tax_deductible"])

    def test_parser_accepts_markdown_fenced_json(self):
        response = '```json\n{"tax_deductible": false}\n```'

        parsed = _parse_json_object(response)

        self.assertFalse(parsed["tax_deductible"])


class AnalyzerClarificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_mode_returns_one_clarification_question(self):
        payload = {
            "extracted_text": "Course fee",
            "store_name": "",
            "receipt_date": None,
            "total_amount": 20,
            "currency": "EUR",
            "items": [],
            "tax_category": "Unklassifiziert",
            "tax_subcategory": "",
            "tax_deductible": False,
            "deduction_notes": "Degree status changes the category.",
            "needs_clarification": True,
            "clarification_question": "What was the purpose of this course?",
        }
        analyzer = make_analyzer(payload)

        result = await analyzer._call_claude(
            "I paid for a training course.",
            True,
        )

        self.assertTrue(result["needs_clarification"])
        self.assertEqual(
            result["clarification_question"],
            "What was the purpose of this course?",
        )
        self.assertIn(CLARIFICATION_PROMPT, analyzer.client.messages.system)
        self.assertEqual(result["clarification_policy"], CLARIFICATION_POLICY_VERSION)

    async def test_control_mode_enforces_one_shot_output(self):
        payload = {
            "tax_category": "Unklassifiziert",
            "tax_subcategory": "",
            "tax_deductible": False,
            "deduction_notes": "Insufficient detail.",
            "needs_clarification": True,
            "clarification_question": "What was it for?",
        }
        analyzer = make_analyzer(payload)

        result = await analyzer._call_claude("Unknown expense", False)

        self.assertTrue(result["analysis_error"])
        self.assertFalse(result["needs_clarification"])
        self.assertIsNone(result["clarification_question"])

    async def test_answered_dialogue_can_finish_with_classification(self):
        payload = {
            "tax_category": "Werbungskosten",
            "tax_subcategory": "Fortbildungskosten",
            "tax_deductible": True,
            "deduction_notes": "Professional training connected to current work.",
            "needs_clarification": False,
            "clarification_question": None,
        }
        analyzer = make_analyzer(payload)

        result = await analyzer._call_claude(
            "Training course fee.",
            True,
            ({"answer": "Professional training required for my current job."},),
        )

        self.assertFalse(result["needs_clarification"])
        self.assertEqual(result["tax_subcategory"], "Fortbildungskosten")
        self.assertIn(CLARIFICATION_PROMPT, analyzer.client.messages.system)

    async def test_photo_is_ner_filtered_before_model_generated_question(self):
        from unittest.mock import AsyncMock, patch
        from taxedo.agent.analyzer import _blank_result
        payload = _blank_result("The purchase purpose is unclear.")
        payload.update(needs_clarification=True, clarification_question="Who was this purchase for?")
        analyzer = make_analyzer(payload)
        analyzer.clarification_enabled = True
        messages = analyzer.client.messages
        messages.create = AsyncMock(wraps=messages.create)

        def ner(text):
            name = "Jane Smith"
            start = text.find(name)
            return types.SimpleNamespace(ents=[] if start < 0 else [types.SimpleNamespace(
                label_="PERSON", start_char=start, end_char=start + len(name)
            )])

        with patch("taxedo.agent.analyzer.extract_attachment", AsyncMock(return_value="Jane Smith\nBread 10.44 EUR")), patch("taxedo.agent.privacy._NER_MODELS", dict.fromkeys(("en", "de"), ner)):
            result = await analyzer.analyze_image(b"image", "Jane Smith bought this")
        sent = messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("Jane Smith", sent)
        self.assertIn("Bread 10.44 EUR", sent)
        self.assertTrue(result["needs_clarification"])
        self.assertEqual(result["clarification_question"], payload["clarification_question"])
        self.assertFalse(result["tax_deductible"])
        self.assertEqual(messages.create.await_count, 2)

    async def test_prior_question_and_answer_reach_model_before_followup(self):
        from unittest.mock import AsyncMock, patch
        from taxedo.agent.analyzer import _blank_result
        payload = _blank_result("One detail remains unclear.")
        payload.update(needs_clarification=True, clarification_question="Was any amount reimbursed?")
        analyzer = make_analyzer(payload)
        messages = analyzer.client.messages
        messages.create = AsyncMock(wraps=messages.create)
        with patch("taxedo.agent.privacy._NER_MODELS", dict.fromkeys(("en", "de"), lambda text: types.SimpleNamespace(ents=[]))):
            result = await analyzer._call_claude("Receipt 10.44 EUR", True, (
                {"question": "What was the purchase for?", "answer": "Supplies for an event."},
            ))
        sent = messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("What was the purchase for?", sent)
        self.assertIn("Supplies for an event.", sent)
        self.assertEqual(result["clarification_question"], "Was any amount reimbursed?")

    async def test_blank_model_question_is_rejected_without_saving(self):
        from taxedo.agent.analyzer import _blank_result
        payload = _blank_result("Unclear.")
        payload.update(needs_clarification=True, clarification_question="   ")
        result = await make_analyzer(payload)._call_claude("Receipt 10.44 EUR", True)
        self.assertTrue(result["analysis_error"])
        self.assertFalse(result["needs_clarification"])

    async def test_receipt_transcription_question_is_reviewed_and_suppressed(self):
        from unittest.mock import AsyncMock
        from taxedo.agent.analyzer import _blank_result, QUESTION_REVIEW_PROMPT
        payload = _blank_result("Merchant and items missing.")
        payload.update(
            needs_clarification=True,
            clarification_question="Can you confirm the merchant name and full list of items purchased?",
            total_amount=10.44,
            items=[{"name": "Bread", "price": 0.99}],
        )
        analyzer = make_analyzer(payload)
        messages = analyzer.client.messages
        messages.reviewed_question = None
        messages.final_payload = dict(payload, needs_clarification=False, clarification_question=None,
                                      tax_category="Nicht abzugsfähig", tax_subcategory="Lebenshaltungskosten",
                                      deduction_notes="Groceries with no stated business purpose.", source_ids=[])
        messages.create = AsyncMock(wraps=messages.create)
        result = await analyzer._call_claude("Bread 0.99, total 10.44", True)
        self.assertEqual(messages.create.await_count, 3)
        self.assertEqual(messages.create.call_args_list[1].kwargs["system"], QUESTION_REVIEW_PROMPT)
        self.assertEqual(result["tax_category"], "Nicht abzugsfähig")
        self.assertIn("Groceries", result["deduction_notes"])
        self.assertFalse(result["analysis_error"])
        self.assertFalse(result["needs_clarification"])
        self.assertIsNone(result["clarification_question"])
        self.assertTrue(result["needs_review"])
        self.assertFalse(result["tax_deductible"])
        self.assertEqual(result["total_amount"], 10.44)
        self.assertEqual(result["items"], payload["items"])

    async def test_question_review_can_narrow_to_missing_context(self):
        from taxedo.agent.analyzer import _blank_result
        payload = _blank_result("Purpose unclear.")
        payload.update(needs_clarification=True, clarification_question="List the items and their business use.")
        analyzer = make_analyzer(payload)
        analyzer.client.messages.reviewed_question = "Was this purchase for your employer or your own household?"
        result = await analyzer._call_claude("Supplies for an event at work", True)
        self.assertEqual(result["clarification_question"], analyzer.client.messages.reviewed_question)
        self.assertTrue(result["needs_clarification"])

    async def test_temperature_is_sent_only_when_configured(self):
        from unittest.mock import AsyncMock
        payload = {
            "tax_category": "Werbungskosten",
            "tax_subcategory": "Arbeitsmittel",
            "tax_deductible": True,
            "deduction_notes": "Supported classification.",
            "needs_clarification": False,
            "clarification_question": None,
        }
        for temperature in (0, None):
            with self.subTest(temperature=temperature):
                analyzer = make_analyzer(payload)
                analyzer.temperature = temperature
                messages = analyzer.client.messages
                messages.create = AsyncMock(wraps=messages.create)

                result = await analyzer._call_claude("Work monitor", False)

                sent = messages.create.call_args.kwargs
                self.assertNotIn("temperature", sent)
                self.assertEqual(
                    sent.get("extra_body"),
                    None if temperature is None else {"temperature": temperature},
                )
                self.assertFalse(result["analysis_error"])

    async def test_missing_initial_references_still_reaches_model(self):
        from taxedo.agent.analyzer import _blank_result
        analyzer = make_analyzer(_blank_result("No supporting references."))
        analyzer.tax_knowledge.search = lambda *args, **kwargs: []
        result = await analyzer._call_claude("Work monitor", False)
        self.assertFalse(result["analysis_error"])
        self.assertTrue(result["needs_review"])
        self.assertIsNotNone(analyzer.client.messages.system)

    async def test_string_boolean_is_rejected(self):
        analyzer = make_analyzer(
            {
                "tax_category": "Werbungskosten",
                "tax_subcategory": "Arbeitsmittel",
                "deduction_notes": "test",
                "tax_deductible": "false",
            }
        )
        result = await analyzer._call_claude("Work monitor", False)
        self.assertTrue(result["analysis_error"])

    async def test_nondeductible_category_cannot_be_deductible_even_with_evidence(self):
        from unittest.mock import AsyncMock

        quote = "An exact reference passage supporting the assessment."
        for deductible in (True, False):
            with self.subTest(deductible=deductible):
                analyzer = make_analyzer({
                    "tax_category": "Nicht abzugsfähig",
                    "tax_subcategory": "Lebenshaltungskosten",
                    "tax_deductible": deductible,
                    "deduction_notes": "Personal purchase.",
                    "source_ids": ["test-source"],
                    "evidence": [{"source_id": "test-source", "quote": quote}],
                })
                references = analyzer.tax_knowledge.search("expense")
                references[0]["text"] = quote
                analyzer.tax_knowledge.search = lambda *args, **kwargs: references
                messages = analyzer.client.messages
                messages.create = AsyncMock(wraps=messages.create)

                result = await analyzer._call_claude("Personal purchase 20 EUR", False)

                self.assertEqual(result["analysis_error"], deductible)
                self.assertFalse(result["tax_deductible"])
                self.assertEqual(messages.create.await_count, 2 if deductible else 1)
                if deductible:
                    self.assertIn("deductible category", result["deduction_notes"])
                else:
                    self.assertEqual(result["source_ids"], ["test-source"])

    async def test_invalid_output_is_repaired_once(self):
        payload = {
            "tax_category": "Werbungskosten",
            "tax_subcategory": "Arbeitsmittel",
            "deduction_notes": "Work monitor",
            "tax_deductible": True,
            "store_name": None,
        }
        analyzer = make_analyzer(payload)
        messages = analyzer.client.messages
        original = messages.create
        calls = []

        async def create(**kwargs):
            calls.append(kwargs["messages"][0]["content"])
            if len(calls) == 1:
                return types.SimpleNamespace(
                    content=[types.SimpleNamespace(text='{"tax_deductible": "yes"}')],
                    usage=types.SimpleNamespace(input_tokens=10, output_tokens=5),
                )
            return await original(**kwargs)

        messages.create = create
        result = await analyzer._call_claude("Work monitor", False)
        self.assertEqual(len(calls), 2)
        self.assertIn("Previous output failed validation", calls[1])
        self.assertFalse(result["analysis_error"])
        self.assertTrue(result["needs_review"])
        self.assertFalse(result["tax_deductible"])

    async def test_incomplete_agent_response_is_retried(self):
        analyzer = make_analyzer(
            {
                "tax_category": "Werbungskosten",
                "tax_subcategory": "Arbeitsmittel",
                "deduction_notes": "Work monitor",
                "tax_deductible": True,
            }
        )
        messages = analyzer.client.messages
        original = messages.create
        calls = []

        async def create(**kwargs):
            calls.append(kwargs["messages"][0]["content"])
            if len(calls) == 1:
                return types.SimpleNamespace(
                    stop_reason="max_tokens",
                    content=[types.SimpleNamespace(type="text", text='{"tax')],
                )
            return await original(**kwargs)

        messages.create = create
        result = await analyzer._call_claude("Work monitor", False)
        self.assertEqual(len(calls), 2)
        self.assertIn("Agent response was incomplete", calls[1])
        self.assertFalse(result["analysis_error"])

    async def test_unretrieved_citation_saves_facts_for_review_without_retry(self):
        analyzer = make_analyzer(
            {
                "tax_category": "Werbungskosten",
                "tax_subcategory": "Arbeitsmittel",
                "tax_deductible": True,
                "deduction_notes": "Unsupported confident claim",
                "source_ids": ["invented"],
                "store_name": "Test shop",
                "total_amount": 689,
                "currency": "EUR",
                "receipt_date": "2026-09-14",
            }
        )
        from unittest.mock import AsyncMock

        messages = analyzer.client.messages
        messages.create = AsyncMock(wraps=messages.create)
        result = await analyzer._call_claude(
            "Work monitor",
            True,
            (
                {
                    "question": "Purpose?",
                    "answer": "I use it both for my work and other",
                },
            ),
        )
        messages.create.assert_awaited_once()
        self.assertFalse(result["analysis_error"])
        self.assertTrue(result["needs_review"])
        self.assertFalse(result["tax_deductible"])
        self.assertEqual(result["total_amount"], 689)
        self.assertEqual(result["source_ids"], [])
        self.assertEqual(result["rag_sources"], [])
        self.assertNotIn("Unsupported confident claim", result["deduction_notes"])


if __name__ == "__main__":
    unittest.main()
