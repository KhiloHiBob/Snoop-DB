import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import httpx
import psycopg
from fastapi.testclient import TestClient

from snoop_db.agent import Agent
from snoop_db.api import create_app
from snoop_db.config import Settings
from snoop_db.generation import DemoProvider, Generation, OpenAIProvider
from snoop_db.validation import UnsafeQuery


SETTINGS = Settings(
    database_url="host=localhost dbname=snoop user=snoop_reader",
    allowed_relations=("public.customers", "public.orders", "public.order_summary"),
)


class AgentTests(unittest.TestCase):
    def make_agent(self, generation):
        provider = Mock()
        provider.generate.return_value = generation
        provider.summarize.return_value = "Approved summary"
        agent = Agent(SETTINGS, provider=provider)
        agent.schema = Mock(return_value={"public.customers": {"columns": [{"name": "id"}]}})
        agent.executor = Mock()
        agent.executor.execute.return_value = {
            "sql": "SELECT id FROM public.customers", "parameters": {},
            "rows": [["private database value"]], "columns": ["id"], "truncated": False,
            "explain": False,
        }
        return agent, provider

    def test_results_are_not_sent_to_provider_by_default(self):
        agent, provider = self.make_agent(Generation(
            kind="query", message="Customers", sql="SELECT id FROM public.customers",
        ))
        response = agent.chat("show customers", [])
        self.assertEqual(response["result"]["rows"], [["private database value"]])
        provider.summarize.assert_not_called()
        self.assertNotIn("private database value", str(provider.generate.call_args))

    def test_explicit_opt_in_enables_result_summary(self):
        agent, provider = self.make_agent(Generation(kind="query", message="Customers", sql="SELECT 1"))
        agent.settings = replace(SETTINGS, send_results_to_llm=True)
        self.assertEqual(agent.chat("show customers", [])["message"], "Approved summary")
        provider.summarize.assert_called_once()

    def test_clarification_and_advice_never_execute(self):
        for kind in ("clarify", "advice", "schema"):
            agent, provider = self.make_agent(Generation(
                kind=kind, message="Clarify first", sql="DROP TABLE public.customers",
            ))
            agent.chat("revenue?", [])
            agent.executor.execute.assert_not_called()

    def test_demo_asks_instead_of_guessing(self):
        demo = DemoProvider()
        self.assertEqual(demo.generate("What is our revenue?", [], {}).kind, "clarify")
        self.assertEqual(demo.generate("What are our best customers?", [], {}).kind, "clarify")

    def test_openai_request_contains_metadata_but_no_credentials(self):
        settings = replace(SETTINGS, provider="openai", llm_api_key="not-a-real-key")
        provider = OpenAIProvider(settings)
        response = httpx.Response(200, request=httpx.Request("POST", "https://example.test"),
                                  json={"choices": [{"message": {"content": json.dumps({
                                      "kind": "clarify", "message": "Which status?",
                                  })}}]})
        with patch("snoop_db.generation.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = response
            result = provider.generate("revenue?", [], {"public.orders": {"columns": []}})
            call = client.return_value.__enter__.return_value.post.call_args
        self.assertEqual(result.kind, "clarify")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer " + settings.llm_api_key)
        payload = json.dumps(call.kwargs["json"])
        self.assertIn("public.orders", payload)
        self.assertNotIn(settings.database_url, payload)
        self.assertNotIn(settings.llm_api_key, payload)
        with self.assertRaises(ValueError):
            provider.summarize("revenue?", {"rows": [["private"]]})

    def test_unknown_dialect_and_empty_allowlist_fail_closed(self):
        with self.assertRaises(ValueError):
            Agent(replace(SETTINGS, dialect="mysql"))
        with self.assertRaises(ValueError):
            replace(SETTINGS, allowed_relations=())


class APITests(unittest.TestCase):
    def setUp(self):
        self.agent = Mock()
        self.client = TestClient(create_app(SETTINGS, self.agent), raise_server_exceptions=False)

    def test_ui_and_chat(self):
        self.agent.chat.return_value = {"kind": "clarify", "message": "Which status?"}
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/chat.js").status_code, 200)
        response = self.client.post("/api/chat", json={"question": "revenue?"})
        self.assertEqual(response.json()["kind"], "clarify")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_invalid_requests(self):
        self.assertEqual(self.client.post("/api/chat", json={"question": ""}).status_code, 422)
        self.assertEqual(self.client.post("/api/chat", json={
            "question": "hello", "history": ["x" * 4001],
        }).status_code, 422)

    def test_errors_do_not_expose_database_or_provider_details(self):
        for error in (psycopg.OperationalError("private connection details"),
                      ValueError("private provider response"),
                      RuntimeError("private unexpected details")):
            self.agent.chat.side_effect = error
            response = self.client.post("/api/chat", json={"question": "show customers"})
            self.assertGreaterEqual(response.status_code, 400)
            self.assertNotIn("private", response.text)

    def test_unsafe_sql_has_clear_refusal(self):
        self.agent.chat.side_effect = UnsafeQuery("Only one read-only SELECT is permitted")
        response = self.client.post("/api/chat", json={"question": "drop everything"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("read-only", response.text)


if __name__ == "__main__":
    unittest.main()
