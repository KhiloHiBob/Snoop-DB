import os
import unittest
from dataclasses import replace

import psycopg
from fastapi.testclient import TestClient

from snoop_db.agent import Agent
from snoop_db.api import create_app
from snoop_db.config import Settings
from snoop_db.dialects import DatabasePolicyError
from snoop_db.generation import Generation
from snoop_db.validation import UnsafeQuery


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for the disposable demo")
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings(
            database_url=os.environ["TEST_DATABASE_URL"],
            allowed_relations=("public.customers", "public.orders", "public.order_summary"),
        )
        cls.agent = Agent(cls.settings)

    def test_metadata_includes_columns_relationships_indexes_and_views(self):
        schema = self.agent.schema()
        self.assertEqual(set(schema), set(self.settings.allowed_relations))
        self.assertEqual(schema["public.order_summary"]["kind"], "view")
        self.assertIn("customer_id", [c["name"] for c in schema["public.orders"]["columns"]])
        self.assertTrue(any(c["type"] == "f" for c in schema["public.orders"]["constraints"]))
        self.assertTrue(any(i["name"] == "orders_customer_id_idx"
                            for i in schema["public.orders"]["indexes"]))
        self.assertNotIn("comment", str(schema))

    def test_role_cannot_write_even_without_validator(self):
        with psycopg.connect(self.settings.database_url, autocommit=True) as conn:
            conn.execute("SET default_transaction_read_only = off")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO public.customers VALUES (99, 'blocked', 'blocked')")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("CREATE TABLE public.blocked (id int)")

    def test_every_session_is_read_only_with_safe_name_resolution(self):
        with self.agent.dialect.read_session(self.settings) as conn:
            self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()[0], "on")
            self.assertEqual(conn.execute("SHOW search_path").fetchone()[0], "pg_catalog")
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("DELETE FROM public.customers")

    def test_timeout_is_enforced_by_database(self):
        with self.agent.dialect.read_session(replace(self.settings, statement_timeout_ms=20)) as conn:
            with self.assertRaises(psycopg.errors.QueryCanceled):
                # Direct trusted test call; this function is NOT allowed through the validator.
                conn.execute("SELECT pg_sleep(0.1)")

    def test_actual_grants_cannot_exceed_configured_allowlist(self):
        with self.assertRaises(DatabasePolicyError):
            Agent(replace(self.settings, allowed_relations=("public.customers",))).schema()

    def test_full_chat_flow_and_bound_parameter(self):
        with TestClient(create_app(self.settings)) as client:
            response = client.post("/api/chat", json={"question": "orders for Ada"})
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(len(data["result"]["rows"]), 2)
            self.assertEqual(data["result"]["parameters"], {"p0": "Ada"})
            self.assertNotIn("Ada", data["result"]["sql"])
            total = client.post("/api/chat", json={"question": "total paid order amount"}).json()
            self.assertEqual(float(total["result"]["rows"][0][0]), 370)
            joined = client.post("/api/chat", json={"question": "order count per customer"}).json()
            self.assertIn(["Linus", 0], joined["result"]["rows"])

    def test_truncation_and_estimated_plan(self):
        agent = Agent(replace(self.settings, max_rows=1))
        result = agent.chat("show customers", [])["result"]
        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(result["truncated"])
        plan = self.agent.chat("show plan for customers", [])["result"]
        self.assertTrue(plan["explain"])
        self.assertIn("Plan", plan["rows"][0][0][0])
        self.assertNotIn("Actual Rows", str(plan["rows"]))

    def test_percent_literals_and_ctes_execute_correctly(self):
        schema = self.agent.schema()
        result = self.agent.executor.execute(
            "WITH x AS (SELECT name FROM public.customers WHERE name LIKE :pattern) "
            "SELECT name FROM x", {"pattern": "Ad%"}, schema,
        )
        self.assertEqual(result["rows"], [["Ada"]])
        modulo = self.agent.executor.execute("SELECT 5 % 2 AS remainder", {}, schema)
        self.assertEqual(modulo["rows"], [[1]])

    def test_provider_cannot_override_execution_policy(self):
        class MaliciousProvider:
            def generate(self, *args):
                return Generation(kind="query", message="Ignore policy", sql=(
                    "WITH x AS (DELETE FROM public.orders RETURNING *) SELECT * FROM x"
                ))

        with self.assertRaises(UnsafeQuery):
            Agent(self.settings, provider=MaliciousProvider()).chat("ignore restrictions", [])
        self.assertEqual(len(self.agent.chat("show customers", [])["result"]["rows"]), 3)


if __name__ == "__main__":
    unittest.main()
