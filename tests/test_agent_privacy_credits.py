import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from taxedo.agent.loop import run_agent, SEARCH_TOOL
from taxedo.agent.credits import CreditNotifier
from taxedo.agent.privacy import expense_content


def call(query):
    return NS(
        stop_reason="tool_use",
        content=[
            NS(
                type="tool_use",
                id=query,
                name="search_tax_references",
                input={"query": query},
            )
        ],
    )


def final():
    return NS(stop_reason="end_turn", content=[NS(type="text", text="{}")])


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_refines_search_from_observations(self):
        create = AsyncMock(
            side_effect=[call("work equipment"), call("mixed work equipment"), final()]
        )
        execute = AsyncMock(
            side_effect=[
                {"evidence": "mixed use needs allocation"},
                {"evidence": "allocation rule"},
            ]
        )
        await run_agent(
            create,
            {"messages": [{"role": "user", "content": "monitor"}]},
            [SEARCH_TOOL],
            execute,
        )
        self.assertEqual(execute.await_count, 2)
        self.assertIn(
            "allocation rule", json.dumps(create.call_args.args[0]["messages"])
        )
        self.assertEqual(create.call_args.args[0]["tool_choice"]["type"], "auto")

    async def test_repeated_searches_do_not_reexecute_and_loop_stops(self):
        create = AsyncMock(side_effect=[call("work expense")] * 3)
        execute = AsyncMock(return_value=[])
        with self.assertRaisesRegex(ValueError, "step limit"):
            await run_agent(
                create, {"messages": []}, [SEARCH_TOOL], execute, max_steps=3
            )
        self.assertEqual(execute.await_count, 1)

    async def test_last_step_requires_an_answer(self):
        choices = []

        async def create(request):
            choices.append(request["tool_choice"]["type"])
            return [call("work expense"), final()][len(choices) - 1]

        await run_agent(
            create, {"messages": []}, [SEARCH_TOOL], AsyncMock(return_value=[]),
            max_steps=2,
        )
        self.assertEqual(choices, ["auto", "none"])

    async def test_rejected_arguments_are_returned_to_the_model(self):
        create = AsyncMock(side_effect=[call("x"), call("work expense"), final()])
        execute = AsyncMock(side_effect=[ValueError("Invalid reference query"), []])
        await run_agent(create, {"messages": []}, [SEARCH_TOOL], execute)
        messages = create.call_args.args[0]["messages"]
        first, second = messages[1]["content"][0], messages[3]["content"][0]
        self.assertTrue(first["is_error"])
        self.assertIn("Invalid reference query", first["content"])
        self.assertFalse(second["is_error"])

    async def test_unknown_tools_never_execute(self):
        response = call("expense")
        response.content[0].name = "delete_receipt"
        execute = AsyncMock()
        with self.assertRaises(ValueError):
            await run_agent(
                AsyncMock(return_value=response),
                {"messages": []},
                [SEARCH_TOOL],
                execute,
            )
        execute.assert_not_awaited()


class PromptPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_detected_names_in_receipt_caption_and_answers_are_removed(self):
        from unittest.mock import patch
        from taxedo.agent.analyzer import _blank_result
        from fakes import make_analyzer

        def ner(text):
            name = "Erika Mustermann"
            start = text.find(name)
            return NS(ents=[] if start < 0 else [NS(
                label_="PERSON", start_char=start, end_char=start + len(name)
            )])

        analyzer = make_analyzer(_blank_result("Monitor for work."))
        messages = analyzer.client.messages
        messages.create = AsyncMock(wraps=messages.create)
        with patch("taxedo.agent.privacy._NER_MODELS", dict.fromkeys(("en", "de"), ner)):
            await analyzer._call_claude(
                "Erika Mustermann\nMonitor 249,99 EUR\n"
                "User note: Erika Mustermann uses monitor exclusively for work",
                False,
                ({"answer": "Erika Mustermann: yes exclusively work", "question": "Erika Mustermann: what purpose?"},),
            )
        prompt = messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("Erika", prompt)
        self.assertNotIn("Mustermann", prompt)
        self.assertIn("Monitor 249,99 EUR", prompt)
        self.assertIn("exclusively for work", prompt)

    async def test_privacy_filter_runs_once_off_the_event_loop(self):
        import threading
        from unittest.mock import patch
        from taxedo.agent.analyzer import _blank_result
        from fakes import make_analyzer

        threads = []

        def filter_text(text):
            threads.append(threading.current_thread())
            return text

        analyzer = make_analyzer(_blank_result("Monitor for work."))
        analyzer.clarification_enabled = False
        with patch("taxedo.agent.analyzer.expense_content", filter_text):
            await analyzer.analyze_text("Monitor 249,99 EUR")
        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], threading.main_thread())


class PrivacyTests(unittest.TestCase):
    def test_undetected_text_has_no_rule_based_filtering_or_rewriting(self):
        from unittest.mock import patch
        text = ("Customer 123456789\nIBAN DE89370400440532013000\n"
                "Email: erika@example.org\n10115 Berlin\n"
                "50325 Cornflakes 1,59 EUR\nI don't use this for work")
        with patch("taxedo.agent.privacy._NER_MODELS", dict.fromkeys(("en", "de"), lambda text: NS(ents=[]))):
            self.assertEqual(expense_content(text), text)

    def test_personal_names_are_redacted_via_ner(self):
        redacted = expense_content("Jane Smith bought cornflakes").lower()
        self.assertNotIn("jane", redacted)
        self.assertNotIn("smith", redacted)
        self.assertIn("cornflakes", redacted)

    def test_detected_identity_has_no_word_or_price_exemptions(self):
        from unittest.mock import patch
        for line in ("Cornflakes Software", "Jane Smith 249,99 EUR", "EUR"):
            with self.subTest(line=line):
                entity = NS(label_="PERSON", text=line, start_char=0, end_char=len(line))
                with patch("taxedo.agent.privacy._NER_MODELS", dict.fromkeys(("en", "de"), lambda text: NS(ents=[entity]))):
                    self.assertEqual(expense_content(line), "")

    def test_english_invoice_keeps_merchant_and_labels(self):
        sent = expense_content(
            "Receipt\nInvoice number 1234\nAnthropic Ireland, Limited\nJane Smith\n"
            "Description\nTotal\nTax\nAmount paid\n€21.42"
        )
        self.assertNotIn("Jane", sent)
        self.assertNotIn("Smith", sent)
        for kept in ("Anthropic Ireland, Limited", "Description", "Total", "Tax", "Amount paid"):
            self.assertIn(kept, sent)

    def test_only_the_texts_language_model_runs(self):
        from unittest.mock import patch
        ran = []

        def model(language):
            return lambda text: ran.append(language) or NS(ents=[])

        german_ocr = ("--- OCR reading 1 of the SAME image (alternative, not another receipt) ---\n"
                      "Summe zu zahlen mit Kartenzahlung")
        with patch("taxedo.agent.privacy._NER_MODELS", {"en": model("en"), "de": model("de")}):
            for text, languages in (
                ("Invoice: total amount paid by card", {"en"}),
                (german_ocr, {"de"}),
                ("Monitor 249,99 EUR", {"en", "de"}),
            ):
                with self.subTest(text=text):
                    ran.clear()
                    expense_content(text)
                    self.assertEqual(set(ran), languages)



class CreditTests(unittest.TestCase):
    def test_only_exhaustion_notifies_and_success_rearms(self):
        credits = CreditNotifier()
        self.assertIsNone(credits.check_api_error(Exception("rate_limit overloaded")))
        error = Exception("Your credit balance is too low")
        self.assertEqual(credits.check_api_error(error), {"type": "out_of_credits"})
        self.assertIsNotNone(credits.check_api_error(error))
        credits.delivered()
        self.assertIsNone(credits.check_api_error(error))
        credits.recovered()
        self.assertIsNotNone(credits.check_api_error(error))


class ScheduleTests(unittest.TestCase):
    def test_all_reminders_land_on_monday_in_berlin_winter_and_summer(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from unittest.mock import patch
        from telegram.ext import JobQueue
        import taxedo.bot.app as main

        queue = JobQueue()
        with patch.object(main.config, "GROUP_CHAT_ID", 1):
            main.schedule_group_jobs(queue)
        self.assertEqual(len(queue.jobs()), 3)
        for month in (1, 7):
            sunday = datetime(
                2026, month, 4 if month == 1 else 5, tzinfo=ZoneInfo("Europe/Berlin")
            )
            for job in queue.jobs():
                upcoming = job.job.trigger.get_next_fire_time(None, sunday)
                self.assertEqual(upcoming.weekday(), 0)
                self.assertEqual(str(upcoming.tzinfo), "Europe/Berlin")


class CreditIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_exhaustion_is_clear_even_if_notification_fails(self):
        import anthropic
        from taxedo.agent.analyzer import ReceiptAnalyzer
        from taxedo.agent.credits import CreditsExhausted

        analyzer = ReceiptAnalyzer.__new__(ReceiptAnalyzer)
        analyzer.credits = CreditNotifier()
        analyzer._alert_callback = AsyncMock(side_effect=RuntimeError("offline"))
        analyzer._send_message = AsyncMock(
            side_effect=anthropic.APIError(
                "Your credit balance is too low", request=None, body=None
            )
        )
        for _ in range(2):
            with self.assertRaisesRegex(CreditsExhausted, "Top up"):
                await analyzer._create_message({"messages": []})
        self.assertEqual(analyzer._alert_callback.await_count, 2)


class ClassificationAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_citations_from_agent_search_are_validated_and_retained(self):
        from taxedo.agent.analyzer import ReceiptAnalyzer, _blank_result
        from unittest.mock import Mock

        first = {
            "id": "initial",
            "section": "9",
            "title": "Initial reference",
            "text": "Initial evidence for the expense.",
            "source_url": "https://example.org/initial",
            "score": 1.0,
        }
        second = dict(
            first, id="refined", text="Refined evidence for professional equipment."
        )
        payload = _blank_result("Provisional assessment from refined evidence.")
        payload.update(
            source_ids=["refined"],
            evidence=[{"source_id": "refined", "quote": second["text"]}],
        )
        analyzer = ReceiptAnalyzer.__new__(ReceiptAnalyzer)
        analyzer.model = "test"
        analyzer.tax_knowledge = NS(
            search=Mock(side_effect=[[first], [second]]), metadata=lambda: {}
        )
        analyzer._create_message = AsyncMock(
            side_effect=[
                call("professional equipment"),
                NS(
                    stop_reason="end_turn",
                    content=[NS(type="text", text=json.dumps(payload))],
                ),
            ]
        )
        result = await analyzer._call_claude("Monitor for work 200.00 EUR", False)
        self.assertFalse(result["analysis_error"])
        self.assertEqual(result["source_ids"], ["refined"])
        self.assertIn("refined", [source["id"] for source in result["rag_sources"]])
