import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from taxedo.runtime.healthcheck import check
from taxedo.storage.receipts import ReceiptStore
from taxedo.agent.explanations import WhyAgent


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ReceiptStore(Path(self.temp.name))
        self.store.init()
        self.receipt_id = self.store.insert_receipt(
            needs_review=True,
            tax_deductible=False,
            tax_category="Werbungskosten",
            tax_subcategory="Arbeitsmittel",
        )

    def test_review_is_atomic_and_audited(self):
        with self.assertRaises(ValueError):
            self.store.review_receipt(
                self.receipt_id,
                42,
                "corrected",
                "checked",
                deductible=True,
                category="unknown",
                subcategory="unknown",
            )
        self.assertTrue(self.store.get_receipt_by_id(self.receipt_id)["needs_review"])
        reviewed = self.store.review_receipt(
            self.receipt_id, 42, "approved", "invoice checked", deductible=True
        )
        self.assertTrue(reviewed["tax_deductible"])
        self.assertFalse(reviewed["needs_review"])
        self.assertEqual(
            self.store.review_history(self.receipt_id)[0]["reviewer_id"], 42
        )
        with self.assertRaises(ValueError):
            self.store.review_receipt(
                self.receipt_id, 42, "approved", "again", deductible=True
            )
        self.assertEqual(len(self.store.review_history(self.receipt_id)), 1)

    def test_health_check(self):
        self.assertFalse(check(Path(self.temp.name)))
        (Path(self.temp.name) / "heartbeat").touch()
        self.assertTrue(check(Path(self.temp.name)))


class FakeAnalyzer:
    model = "test-model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.tax_knowledge = NS(
            available=True,
            search=lambda query, limit: [
                {"id": "source-1", "title": "Reference", "text": "Relevant passage"}
            ],
        )

    async def _create_message(self, request):
        json.dumps(request)
        return next(self.responses)


def tool_response(name, payload):
    return NS(
        stop_reason="tool_use",
        content=[NS(type="tool_use", id="call-1", name=name, input=payload)],
    )


class WhyTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_tools_and_citations(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ReceiptStore(Path(temp))
            store.init()
            receipt_id = store.insert_receipt(needs_review=True, tax_deductible=False)
            final = NS(
                stop_reason="end_turn",
                content=[
                    NS(
                        type="text",
                        text=json.dumps(
                            {
                                "explanation": "Still needs review.",
                                "source_ids": ["source-1"],
                            }
                        ),
                    )
                ],
            )
            agent = WhyAgent(
                store,
                FakeAnalyzer(
                    [
                        tool_response("get_receipt", {"receipt_id": receipt_id}),
                        tool_response(
                            "search_tax_references", {"query": "tax expense"}
                        ),
                        final,
                    ]
                ),
            )
            self.assertIn("source-1", await agent.explain(receipt_id))
            analyzer = FakeAnalyzer(
                [tool_response("get_receipt", {"receipt_id": receipt_id + 1}), final]
            )
            analyzer._create_message = AsyncMock(wraps=analyzer._create_message)
            with self.assertRaisesRegex(ValueError, "did not read"):
                await WhyAgent(store, analyzer).explain(receipt_id)
            result = analyzer._create_message.call_args.args[0]["messages"][-1]["content"][0]
            self.assertTrue(result["is_error"])
            self.assertIn("different receipt", result["content"])
            self.assertTrue(store.get_receipt_by_id(receipt_id)["needs_review"])

    async def test_malformed_json_is_repaired_once_with_same_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ReceiptStore(Path(temp))
            store.init()
            receipt_id = store.insert_receipt(needs_review=True, review_reason="Checked invoice")
            analyzer = FakeAnalyzer([
                tool_response("get_receipt", {"receipt_id": receipt_id}),
                tool_response("search_tax_references", {"query": "work expenses"}),
                NS(stop_reason="end_turn", content=[NS(type="text", text='{"explanation": broken')]),
                NS(stop_reason="end_turn", content=[NS(type="text", text='{"explanation":"Needs review.","source_ids":["source-1"]}')]),
            ])
            analyzer._create_message = AsyncMock(wraps=analyzer._create_message)
            before = store.get_receipt_by_id(receipt_id)
            result = await WhyAgent(store, analyzer).explain(receipt_id)
            self.assertIn("Needs review.", result)
            self.assertEqual(analyzer._create_message.await_count, 4)
            repair = analyzer._create_message.call_args.args[0]
            self.assertNotIn("tools", repair)
            self.assertIn("Relevant passage", repair["messages"][0]["content"])
            self.assertEqual(before, store.get_receipt_by_id(receipt_id))

    async def test_failed_repair_and_invented_citations_are_rejected(self):
        for repaired in ('still not JSON', '{"explanation":"Test","source_ids":["invented"]}'):
            with self.subTest(repaired=repaired), tempfile.TemporaryDirectory() as temp:
                store = ReceiptStore(Path(temp))
                store.init()
                receipt_id = store.insert_receipt(needs_review=True)
                analyzer = FakeAnalyzer([
                    tool_response("get_receipt", {"receipt_id": receipt_id}),
                    NS(stop_reason="end_turn", content=[NS(type="text", text="bad JSON")]),
                    NS(stop_reason="end_turn", content=[NS(type="text", text=repaired)]),
                ])
                with self.assertRaises(ValueError):
                    await WhyAgent(store, analyzer).explain(receipt_id)


class WhyRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_loop_can_run_while_search_waits(self):
        loop = asyncio.get_running_loop()
        released = threading.Event()

        def search(query, limit):
            loop.call_soon_threadsafe(released.set)
            if not released.wait(1):
                raise AssertionError("Retrieval blocked the event loop")
            return [{"id": "source", "text": "Evidence"}]

        def tool(name, payload):
            return NS(
                stop_reason="tool_use",
                content=[NS(type="tool_use", id=name, name=name, input=payload)],
            )

        responses = [
            tool("get_receipt", {"receipt_id": 1}),
            tool("search_tax_references", {"query": "expense"}),
            NS(
                stop_reason="end_turn",
                content=[
                    NS(
                        type="text",
                        text='{"explanation":"Review needed","source_ids":["source"]}',
                    )
                ],
            ),
        ]
        analyzer = NS(
            model="test",
            tax_knowledge=NS(available=True, search=search),
            _create_message=AsyncMock(side_effect=responses),
        )
        agent = WhyAgent(NS(get_receipt_by_id=lambda _: {"id": 1}), analyzer)
        self.assertIn("Review needed", await agent.explain(1))

        responses[-1] = NS(stop_reason="end_turn", content=[NS(type="text", text="[]")])
        analyzer._create_message = AsyncMock(side_effect=responses)
        with self.assertRaisesRegex(ValueError, "JSON object"):
            await agent.explain(1)
