import pytest

from db_dog.safety.sql_guard import guard_sql, has_company_id_predicate

SAFE = [
    "SELECT 1",
    "select * from employees where company_id = 1",
    "SELECT department, count(*) FROM employees WHERE company_id = 1 GROUP BY department",
    "WITH x AS (SELECT id FROM employees WHERE company_id = 1) SELECT * FROM x",
    "SELECT a FROM t1 UNION SELECT a FROM t2",
    "SELECT * FROM information_schema.columns WHERE table_name = 'employees'",
    "SELECT * FROM pg_catalog.pg_tables",
    "EXPLAIN SELECT * FROM employees WHERE company_id = 1",
    "EXPLAIN (FORMAT JSON) SELECT 1",
    "SELECT 1;",
    "SELECT profile->>'office' FROM employees WHERE company_id = 1",
]

UNSAFE = [
    ("", "empty"),
    ("SELECT 1; DROP TABLE employees", "one statement"),
    ("SELECT 1; SELECT 2", "one statement"),
    ("DELETE FROM employees", "only SELECT"),
    ("UPDATE employees SET salary = 0", "only SELECT"),
    ("INSERT INTO employees VALUES (1)", "only SELECT"),
    ("DROP TABLE employees", "only SELECT"),
    ("TRUNCATE employees", "only SELECT"),
    ("ALTER TABLE employees ADD COLUMN x int", "only SELECT"),
    ("CREATE TABLE x (id int)", "only SELECT"),
    ("GRANT ALL ON employees TO public", "only SELECT"),
    ("COPY employees TO STDOUT", "only SELECT"),
    ("SET ROLE admin", "only SELECT"),
    ("DO $$ BEGIN PERFORM 1; END $$", "only SELECT"),
    ("VACUUM employees", "only SELECT"),
    ("WITH d AS (DELETE FROM employees RETURNING *) SELECT * FROM d", "not allowed"),
    ("WITH u AS (UPDATE employees SET salary = 1 RETURNING *) SELECT * FROM u", "not allowed"),
    ("SELECT * INTO backup FROM employees", "not allowed"),
    ("SELECT * FROM employees FOR UPDATE", "not allowed"),
    ("SELECT * FROM employees FOR SHARE", "not allowed"),
    ("SELECT pg_sleep(30)", "pg_sleep"),
    ("SELECT pg_catalog.pg_sleep(30)", "pg_sleep"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT lo_import('/etc/passwd')", "lo_import"),
    ("SELECT dblink('host=x', 'select 1')", "dblink"),
    ("SELECT set_config('default_transaction_read_only', 'off', false)", "set_config"),
    ("SELECT nextval('employees_id_seq')", "nextval"),
    ("SELECT pg_advisory_lock(1)", "pg_advisory"),
    ("SELECT pg_terminate_backend(123)", "pg_terminate_backend"),
    ("SELECT query_to_xml('delete from employees', true, true, '')", "query_to_xml"),
    ("EXPLAIN ANALYZE SELECT 1", "EXPLAIN ANALYZE"),
    ("EXPLAIN (ANALYZE) SELECT 1", "EXPLAIN ANALYZE"),
    ("EXPLAIN (ANALYZE true, BUFFERS) SELECT 1", "EXPLAIN ANALYZE"),
    ("EXPLAIN ANALYSE SELECT 1", "EXPLAIN ANALYZE"),
    ("EXPLAIN DELETE FROM employees", "only SELECT"),
    ("EXPLAIN (FORMAT JSON; DROP TABLE x) SELECT 1", "unsupported"),
    ("SELEC nonsense (((", "could not parse"),
]


@pytest.mark.parametrize("sql", SAFE)
def test_safe_statements_pass(sql):
    r = guard_sql(sql)
    assert r.ok, r.reason


@pytest.mark.parametrize("sql,fragment", UNSAFE)
def test_unsafe_statements_rejected(sql, fragment):
    r = guard_sql(sql)
    assert not r.ok
    assert fragment.lower() in r.reason.lower()


def test_limit_is_injected_and_capped():
    assert guard_sql("SELECT id FROM employees", max_rows=500).sql.endswith("LIMIT 500")
    assert guard_sql("SELECT id FROM employees LIMIT 5").sql.endswith("LIMIT 5")
    assert guard_sql("SELECT id FROM employees LIMIT 100000", max_rows=500).sql.endswith("LIMIT 500")


def test_limit_forced_flag():
    assert guard_sql("SELECT id FROM employees").limit_forced
    assert guard_sql("SELECT id FROM employees LIMIT 100000", max_rows=500).limit_forced
    assert not guard_sql("SELECT id FROM employees LIMIT 5").limit_forced      # the user's own smaller limit
    assert not guard_sql("EXPLAIN SELECT id FROM employees").limit_forced


def test_hit_forced_limit_helper():
    from db_dog.connectors.base import hit_forced_limit
    g = guard_sql("SELECT id FROM employees", max_rows=500)
    assert hit_forced_limit(g, 500) and not hit_forced_limit(g, 499)
    assert not hit_forced_limit(guard_sql("SELECT id FROM employees LIMIT 5"), 5)   # user asked for exactly 5
    assert not hit_forced_limit(None, 10)


def test_select_star_gets_the_smaller_limit():
    r = guard_sql("SELECT * FROM employees", max_rows=500, star_rows=50)
    assert r.selects_star and r.sql.endswith("LIMIT 50")
    assert not guard_sql("SELECT count(*) FROM employees").selects_star


def test_set_operations_are_wrapped_so_the_limit_applies():
    r = guard_sql("SELECT a FROM t1 UNION ALL SELECT a FROM t2", max_rows=10)
    assert r.ok and r.sql.endswith("LIMIT 10") and "_dbdog_q" in r.sql


def test_comments_are_never_forwarded():
    r = guard_sql("SELECT /* x */ 1 -- trailing\n")
    assert r.ok and "/*" not in r.sql and "--" not in r.sql


def test_metadata_detection():
    assert guard_sql("SELECT * FROM information_schema.tables").is_metadata
    assert not guard_sql("SELECT * FROM employees").is_metadata
    assert not guard_sql("SELECT 1").is_metadata


def test_tables_are_reported_without_ctes():
    r = guard_sql("WITH x AS (SELECT id FROM employees) SELECT * FROM x JOIN companies c ON true")
    assert r.tables == ["companies", "employees"]


def test_explain_keeps_the_guarded_statement():
    r = guard_sql("EXPLAIN SELECT id FROM employees")
    assert r.ok and r.kind == "explain" and r.sql.startswith("EXPLAIN SELECT")


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1 FROM t WHERE company_id = 7", True),
    ("SELECT 1 FROM t WHERE company_id IN (1, 2)", True),
    ("SELECT 1 FROM t WHERE 7 = company_id", True),
    ("SELECT 1 FROM t WHERE company_id = $1", True),
    ("SELECT 1 FROM t WHERE company_id IS NOT NULL", False),
    ("SELECT 1 FROM a JOIN b ON a.company_id = b.company_id", False),
    ("SELECT 1 FROM t WHERE name = 'x'", False),
    ("SELECT 1 FROM t", False),
])
def test_company_id_predicate_detection(sql, expected):
    assert has_company_id_predicate(guard_sql(sql).ast) is expected


def test_mysql_show_and_describe():
    assert guard_sql("SHOW TABLES", dialect="mysql").ok
    assert guard_sql("DESCRIBE employees", dialect="mysql").kind == "show"
    assert not guard_sql("SHOW TABLES", dialect="postgres").ok
    assert not guard_sql("SHOW GRANTS", dialect="mysql").ok
    assert not guard_sql("SHOW TABLES; DROP TABLE x", dialect="mysql").ok
