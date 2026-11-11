"""Allowlist-style SQL guard built on sqlglot.

Rules:
  * exactly one statement;
  * only SELECT (CTEs and set operations included), ``EXPLAIN`` (never ``ANALYZE``) and, for
    MySQL-protocol stores, a small set of SHOW / DESCRIBE forms;
  * no data-modifying or DDL nodes anywhere in the tree (this also catches data-modifying CTEs);
  * no ``SELECT INTO`` and no row-locking clauses (``FOR UPDATE`` / ``FOR SHARE``);
  * no functions that sleep, touch files, take locks, call out to other servers or mutate state.

The SQL that gets executed is the re-serialised AST, never the raw input, so the guard always
inspects exactly what runs (comments and odd whitespace cannot smuggle anything past it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_FORBIDDEN_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "AlterColumn",
    "Command", "Copy", "Into", "Lock", "Set", "SetItem", "Transaction", "Commit", "Rollback",
    "TruncateTable", "Grant", "Revoke", "Use", "Pragma", "Analyze", "Refresh", "Kill", "Declare",
    "LoadData", "Attach", "Detach", "Cache", "Uncache", "Comment", "Vacuum",
)
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, n) for n in _FORBIDDEN_NAMES if hasattr(exp, n)
)

# Exact function names that are never allowed.
BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "sleep", "benchmark",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "pg_ls_logdir",
    "pg_ls_waldir", "pg_ls_tmpdir", "pg_ls_archive_statusdir",
    "set_config", "nextval", "setval", "pg_notify", "pg_reload_conf", "pg_rotate_logfile",
    "pg_terminate_backend", "pg_cancel_backend", "pg_switch_wal", "pg_create_restore_point",
    "txid_current", "pg_current_xact_id", "pg_replication_slot_advance",
    "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema", "table_to_xml",
    "table_to_xml_and_xmlschema", "cursor_to_xml", "database_to_xml", "schema_to_xml",
    "load_file", "get_lock", "release_lock", "is_free_lock",
}
# Any function whose name starts with one of these is blocked.
BLOCKED_FUNCTION_PREFIXES = ("lo_", "dblink", "pg_advisory", "pg_logical", "pg_file", "pg_try_advisory")

_EXPLAIN_RE = re.compile(r"^\s*explain\s*(?:\((?P<opts>[^)]*)\))?\s*(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_EXPLAIN_OPTS_RE = re.compile(r"^[\w\s,]*$")
_SHOW_RE = re.compile(
    r"^\s*(?:show\s+(?:full\s+)?(?:tables|databases|columns\s+from\s+[\w.`]+|create\s+table\s+[\w.`]+|"
    r"indexes?\s+from\s+[\w.`]+|table\s+status)(?:\s+(?:from|in)\s+[\w`]+)?(?:\s+like\s+'[^';]*')?"
    r"|(?:describe|desc)\s+[\w.`]+)\s*;?\s*$",
    re.IGNORECASE,
)
_METADATA_SCHEMAS = {"information_schema", "pg_catalog"}


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""                     # the SQL to execute (re-serialised, possibly with a LIMIT)
    reason: str = ""                  # why it was rejected
    kind: str = "select"              # select | explain | show
    tables: list[str] = field(default_factory=list)
    is_metadata: bool = False         # only touches information_schema / pg_catalog
    selects_star: bool = False
    ast: exp.Expression | None = None
    limit_applied: int | None = None
    limit_forced: bool = False        # True when Snoop-DB added or lowered the LIMIT (so hitting it means "maybe more")


def _reject(reason: str) -> GuardResult:
    return GuardResult(ok=False, reason=reason)


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    try:
        return str(node.sql_name()).lower()
    except Exception:  # pragma: no cover - defensive
        return type(node).__name__.lower()


def _check_functions(tree: exp.Expression) -> str | None:
    for node in tree.walk():
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_FUNCTION_PREFIXES):
                return f"function '{name}' is not allowed"
    return None


def _selects_star(tree: exp.Expression) -> bool:
    if not isinstance(tree, exp.Select):
        return False
    for e in tree.expressions:
        if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
            return True
    return False


def _set_operation_type() -> type[exp.Expression]:
    return getattr(exp, "SetOperation", exp.Union)


def _apply_limit(tree: exp.Expression, max_rows: int, star_rows: int) -> tuple[exp.Expression, int, bool]:
    """Return (tree, effective limit, forced). ``forced`` means we added or lowered the limit."""
    cap = min(max_rows, star_rows) if _selects_star(tree) else max_rows
    limit = tree.args.get("limit")
    if limit is not None:
        expr = limit.args.get("expression")
        if isinstance(expr, exp.Literal) and expr.is_int:
            if int(expr.this) <= cap:
                return tree, int(expr.this), False   # the user's own, smaller limit
            return tree.limit(cap), cap, True
        return tree.limit(cap), cap, True  # non-literal limit: override
    if isinstance(tree, _set_operation_type()):
        return exp.select("*").from_(tree.subquery("_snoop_q")).limit(cap), cap, True
    return tree.limit(cap), cap, True


def guard_sql(sql: str, dialect: str = "postgres", max_rows: int = 500, star_rows: int = 50) -> GuardResult:
    """Validate ``sql`` and return the SQL that is safe to run."""
    if not sql or not sql.strip():
        return _reject("empty statement")

    # EXPLAIN is handled textually, then the inner statement goes through the full guard.
    m = _EXPLAIN_RE.match(sql)
    if m:
        opts = (m.group("opts") or "").strip()
        rest = (m.group("rest") or "").strip()
        if opts and not _EXPLAIN_OPTS_RE.match(opts):
            return _reject("unsupported EXPLAIN options")
        if re.search(r"\banaly[sz]e\b", opts, re.IGNORECASE) or re.match(r"analy[sz]e\b", rest, re.IGNORECASE):
            return _reject("EXPLAIN ANALYZE executes the query and is not allowed; use plain EXPLAIN")
        inner = guard_sql(rest, dialect, max_rows, star_rows)
        if not inner.ok:
            return inner
        prefix = f"EXPLAIN ({opts}) " if opts else "EXPLAIN "
        # Keep the original LIMIT behaviour out of EXPLAIN output: explain the statement as written.
        return GuardResult(
            ok=True, sql=prefix + inner.sql, kind="explain", tables=inner.tables,
            is_metadata=inner.is_metadata, ast=inner.ast, limit_applied=inner.limit_applied,
        )

    if _SHOW_RE.match(sql):
        if dialect not in ("mysql", "singlestore"):
            return _reject("SHOW / DESCRIBE are only available on MySQL-protocol stores; query information_schema instead")
        return GuardResult(ok=True, sql=sql.strip().rstrip(";"), kind="show", is_metadata=True)

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as e:
        return _reject(f"could not parse the statement: {str(e).splitlines()[0][:200]}")
    except Exception as e:  # sqlglot can raise non-Sqlglot errors on odd input
        return _reject(f"could not parse the statement: {type(e).__name__}")

    if len(statements) != 1:
        return _reject("exactly one statement is allowed")
    tree = statements[0]

    allowed_roots = (exp.Select, _set_operation_type())
    if not isinstance(tree, allowed_roots):
        return _reject(f"only SELECT statements are allowed (got {type(tree).__name__.upper()})")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _reject(f"{type(node).__name__.upper()} is not allowed in a read-only query")

    bad_fn = _check_functions(tree)
    if bad_fn:
        return _reject(bad_fn)

    cte_names = {str(c.alias).lower() for c in tree.find_all(exp.CTE)}
    tables: list[str] = []
    schemas: set[str] = set()
    for t in tree.find_all(exp.Table):
        name = str(t.name).lower()
        if not name or name in cte_names:
            continue
        tables.append(name)
        schemas.add(str(t.args["db"].name).lower() if t.args.get("db") is not None else "")
    is_metadata = bool(tables) and schemas <= _METADATA_SCHEMAS and "" not in schemas

    for node in tree.walk():  # never forward comments to the database
        node.comments = None

    star = _selects_star(tree)
    limited, applied, forced = _apply_limit(tree, max_rows, star_rows)
    try:
        final_sql = limited.sql(dialect=dialect)
    except Exception as e:  # pragma: no cover - defensive
        return _reject(f"could not re-serialise the statement: {type(e).__name__}")

    return GuardResult(
        ok=True, sql=final_sql, kind="select", tables=sorted(set(tables)), is_metadata=is_metadata,
        selects_star=star, ast=limited, limit_applied=applied, limit_forced=forced,
    )


def has_company_id_predicate(tree: exp.Expression | None) -> bool:
    """True when the tree compares ``company_id`` to a non-column value (literal, param, subquery)."""
    if tree is None:
        return False

    def is_company_col(e: exp.Expression | None) -> bool:
        return isinstance(e, exp.Column) and str(e.name).lower() == "company_id"

    for node in tree.find_all(exp.EQ, exp.In):
        if isinstance(node, exp.EQ):
            a, b = node.this, node.expression
            if is_company_col(a) and not isinstance(b, exp.Column):
                return True
            if is_company_col(b) and not isinstance(a, exp.Column):
                return True
        else:  # IN
            if is_company_col(node.this) and not any(isinstance(x, exp.Column) for x in node.expressions):
                return True
    return False
