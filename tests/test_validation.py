import unittest

from snoop_db.validation import UnsafeQuery, validate_sql
from snoop_db.dialects import PostgresDialect


SCHEMA = {
    "public.customers": {"columns": [{"name": "id"}, {"name": "name"}]},
    "public.orders": {"columns": [{"name": "customer_id"}, {"name": "total"}]},
}


class ValidationTests(unittest.TestCase):
    def test_select_join_cte_and_bound_values(self):
        query = validate_sql("""
            WITH totals AS (
                SELECT customer_id, SUM(total) AS amount
                FROM public.orders GROUP BY customer_id
            )
            SELECT c.name, t.amount FROM public.customers c
            JOIN totals t ON c.id = t.customer_id WHERE c.name = :name AND t.amount > 10
        """, {"name": "O'Reilly; DROP TABLE customers; --"}, SCHEMA)
        self.assertNotIn("O'Reilly", query.sql)
        self.assertEqual(set(query.parameters.values()), {"O'Reilly; DROP TABLE customers; --", 10})

    def test_inlined_literals_are_bound(self):
        query = validate_sql("SELECT * FROM public.customers WHERE name = 'Ada'", {}, SCHEMA)
        self.assertNotIn("'Ada'", query.sql)
        self.assertEqual(query.parameters, {"p0": "Ada"})

    def test_unsafe_queries_are_rejected(self):
        queries = [
            "DELETE FROM public.customers",
            "INSERT INTO public.customers VALUES (1)",
            "UPDATE public.customers SET name = 'x'",
            "DROP TABLE public.customers",
            "ALTER TABLE public.customers ADD secret text",
            "TRUNCATE public.customers",
            "VACUUM public.customers",
            "COPY public.customers TO '/tmp/leak'",
            "SET default_transaction_read_only = off",
            "EXPLAIN ANALYZE SELECT * FROM public.customers",
            "SELECT 1; SELECT 2",
            "SELECT 1; -- hidden\n DELETE FROM public.customers",
            "SELECT * INTO TEMP tmp FROM public.customers",
            "SELECT * FROM public.customers FOR UPDATE",
            "SELECT * FROM public.customers FOR SHARE",
            "WITH x AS (DELETE FROM public.customers RETURNING *) SELECT * FROM x",
            "WITH x AS (INSERT INTO public.customers VALUES (1) RETURNING *) SELECT * FROM x",
            "SELECT pg_sleep(20)",
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT pg_advisory_lock(1)",
            "SELECT set_config('search_path', 'public', false)",
            "SELECT nextval('x')",
            "SELECT public.lower(name) FROM public.customers",
            "SELECT dblink('secret', 'DELETE FROM users')",
            "SELECT * FROM generate_series(1, 100000000)",
            "SELECT * FROM pg_catalog.pg_authid",
            "SELECT * FROM customers",
            "SELECT * FROM public.secrets",
            "SELECT * FROM other.public.customers",
            "WITH secrets AS (SELECT * FROM public.secrets) SELECT * FROM secrets",
            "WITH customers AS (SELECT * FROM public.secrets) SELECT * FROM customers",
            "SELECT CAST('x' AS regclass)",
            "SELECT 'x'::public.custom_type",
            "SELECT CURRENT_USER",
            "WITH RECURSIVE t AS (SELECT 1 UNION ALL SELECT * FROM t) SELECT * FROM t",
            "SELECT * FROM public.customers UNION SELECT * FROM public.secrets",
            "SELECT * FROM public.customers WHERE EXISTS (SELECT 1 FROM public.secrets)",
            "SELECT missing_column FROM public.customers",
            "SELECT * FROM public.customers ORDER BY 1",
            "SELECT name FROM public.customers GROUP BY 1",
            "SELECT * FROM public.secrets x JOIN public.customers x ON true",
            "SELECT s.name, s.setting FROM pg_catalog.pg_settings s "
            "ANTI JOIN public.customers s ON FALSE",
            "SELECT s.name FROM public.secrets s SEMI JOIN public.customers s ON TRUE",
            "SELECT * FROM public.customers c ANTI JOIN pg_catalog.pg_settings s ON FALSE",
            "SELECT CAST(name AS VARCHAR(:length)) FROM public.customers",
            "SELECT CAST(name AS VARCHAR(10000000)) FROM public.customers",
            'WITH "PG_SETTINGS" AS (SELECT TRUE AS ignored) SELECT * FROM PG_SETTINGS',
            'WITH "CUSTOMERS" AS (SELECT TRUE AS ignored) SELECT * FROM CUSTOMERS',
        ]
        for query in queries:
            with self.subTest(query=query), self.assertRaises(UnsafeQuery):
                validate_sql(query, {}, SCHEMA)

    def test_parameters_fail_closed(self):
        for values in ({}, {"x": []}, {"x": float("nan")}, {"x": 1, "unused": 2}):
            with self.subTest(values=values), self.assertRaises(UnsafeQuery):
                validate_sql("SELECT :x", values, SCHEMA)

    def test_single_select_union_and_cast(self):
        for query in ("SELECT 1", "SELECT CAST(:x AS INTEGER)",
                      "SELECT id FROM public.customers UNION SELECT customer_id FROM public.orders"):
            result = validate_sql(query, {"x": 2} if ":x" in query else {}, SCHEMA)
            self.assertTrue(result.sql.startswith("SELECT"))

    def test_comments_are_not_forwarded(self):
        result = validate_sql("SELECT 1 /* private comment */", {}, SCHEMA)
        self.assertNotIn("private", result.sql)

    def test_driver_binding_preserves_generated_placeholders(self):
        result = validate_sql("SELECT :value % 2", {"value": 5}, {})
        sql, parameters = PostgresDialect().bind(result.sql, result.parameters)
        self.assertIn("%(p0)s", sql)
        self.assertIn(" %% ", sql)
        self.assertEqual(parameters, {"p0": 5, "p1": 2})

    def test_numeric_type_modifiers_remain_syntax(self):
        for cast in ("DECIMAL(12, 2)", "VARCHAR(10)", "TIMESTAMP(3)"):
            result = validate_sql(f"SELECT CAST(:value AS {cast})",
                                  {"value": "12.34"}, {})
            self.assertIn(cast, result.sql)
            self.assertEqual(result.parameters, {"p0": "12.34"})

    def test_identifiers_follow_postgres_case_rules(self):
        for query in (
            "SELECT ID FROM PUBLIC.CUSTOMERS",
            "WITH CUSTOMERS AS (SELECT id FROM public.customers) SELECT id FROM customers",
            'WITH "CUSTOMERS" AS (SELECT id FROM public.customers) SELECT id FROM "CUSTOMERS"',
        ):
            result = validate_sql(query, {}, SCHEMA)
            self.assertIn("public.customers", result.sql)


if __name__ == "__main__":
    unittest.main()
