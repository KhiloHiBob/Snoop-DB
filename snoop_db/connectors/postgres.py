"""PostgreSQL connector. Read-only at the session level *and* through the SQL guard."""

from __future__ import annotations

import time
from typing import Any

from ..config import Config, EnvConfig, resolve_secret
from ..preflight import require
from ..safety.policy import Prepared, prepare_sql
from .base import QueryResult, hit_forced_limit, shape_rows

_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


class PolicyError(ValueError):
    """A statement was rejected by the SQL guard or query policy."""


class PostgresConnector:
    store = "postgres"
    dialect = "postgres"

    def __init__(self, config: Config, env: EnvConfig):
        self.config = config
        self.env = env
        self.cfg = env.stores["postgres"]
        self._company_tables: dict[str, set[str]] = {}

    # ------------------------------------------------------------------ connection
    def _connect(self, database: str | None = None):
        import psycopg  # imported lazily so the guard/tests work without a driver

        host, port = self.cfg.get("host", "localhost"), self.cfg.get("port")
        require(self.env, "postgres", host, port)
        db = database or self.cfg.get("default_database")
        if not db:
            raise ValueError("no database given and no default_database configured")
        timeout = int(self.config.limits["statement_timeout_ms"])
        conn = psycopg.connect(
            host=host,
            port=int(port),
            user=self.cfg.get("user") or None,
            password=resolve_secret(self.cfg, "password"),
            dbname=db,
            connect_timeout=5,
            application_name="snoop-db",
            options=f"-c default_transaction_read_only=on -c statement_timeout={timeout} "
                    f"-c idle_in_transaction_session_timeout={timeout}",
            autocommit=False,
        )
        conn.read_only = True
        return conn

    def _run_internal(self, database: str | None, sql: str, params: tuple[Any, ...] = ()) -> list[tuple]:
        """Run a trusted, fixed catalog query (never user text) in a read-only transaction."""
        conn = self._connect(database)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or None)  # None: a literal % is then left alone
                return cur.fetchall()
        finally:
            conn.rollback()
            conn.close()

    # ------------------------------------------------------------------ discovery
    def list_databases(self) -> list[str]:
        excluded = set(self.config.exclude_databases)
        rows = self._run_internal(
            self.cfg.get("default_database"),
            "SELECT datname FROM pg_database WHERE NOT datistemplate "
            "AND datname NOT LIKE 'pg\\_%' ORDER BY 1",
        )
        return [r[0] for r in rows if r[0] not in excluded]

    def list_tables(self, database: str, schema: str = "public", like: str | None = None) -> list[dict[str, Any]]:
        rows = self._run_internal(
            database,
            "SELECT c.relname, c.reltuples::bigint FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind IN ('r','p') AND c.relname ILIKE %s "
            "ORDER BY 1 LIMIT 1000",
            (schema, f"%{like}%" if like else "%"),
        )
        return [{"table": r[0], "approx_rows": max(int(r[1]), 0)} for r in rows]

    def describe(self, database: str, table: str, schema: str = "public") -> dict[str, Any]:
        cols = self._run_internal(
            database,
            "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (schema, table),
        )
        if not cols:
            raise ValueError(f"table {schema}.{table} not found in database {database}")
        idx = self._run_internal(
            database,
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s ORDER BY 1",
            (schema, table),
        )
        return {
            "table": f"{schema}.{table}",
            "columns": [
                {"name": c[0], "type": c[1], "nullable": c[2] == "YES", "default": c[3]} for c in cols
            ],
            "indexes": [{"name": i[0], "definition": i[1]} for i in idx],
            "has_company_id": any(c[0] == "company_id" for c in cols),
        }

    def company_id_tables(self, database: str) -> set[str]:
        if database not in self._company_tables:
            rows = self._run_internal(
                database,
                "SELECT DISTINCT table_name FROM information_schema.columns "
                "WHERE column_name = 'company_id' AND table_schema <> ALL(%s)",
                (list(_SYSTEM_SCHEMAS),),
            )
            self._company_tables[database] = {r[0].lower() for r in rows}
        return self._company_tables[database]

    # ------------------------------------------------------------------ querying
    def prepare(self, database: str, sql: str) -> Prepared:
        return prepare_sql(sql, self.dialect, self.config, self.env, self.company_id_tables(database))

    def query(self, database: str | None, sql: str) -> QueryResult:
        db = database or self.cfg.get("default_database")
        prepared = self.prepare(db, sql)
        if not prepared.ok:
            raise PolicyError(prepared.reason)
        lim = self.config.limits
        started = time.monotonic()
        conn = self._connect(db)
        try:
            with conn.cursor() as cur:
                cur.execute(prepared.sql)
                columns = [d.name for d in cur.description] if cur.description else []
                raw = cur.fetchmany(int(lim["max_rows"]) + 1) if cur.description else []
        finally:
            conn.rollback()  # always: nothing is ever committed
            conn.close()
        truncated_rows = len(raw) > int(lim["max_rows"])
        rows, truncated_bytes = shape_rows(raw[: int(lim["max_rows"])], int(lim["max_cell_chars"]), int(lim["max_result_bytes"]))
        notes = list(prepared.warnings)
        hit_limit = hit_forced_limit(prepared.guard, len(raw))
        if hit_limit:
            notes.append(f"row limit {prepared.guard.limit_applied} reached; more rows may exist")
        if truncated_bytes:
            notes.append("result truncated at the byte budget")
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated_rows or truncated_bytes or hit_limit,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            executed=prepared.sql,
            notes=notes,
        )

    # ------------------------------------------------------------------ health
    def health(self, database: str | None = None) -> dict[str, Any]:
        """Grant + replica check, as in the db-connection skill: warn if the role could write."""
        row = self._run_internal(
            database,
            "SELECT current_user, pg_is_in_recovery(), current_setting('default_transaction_read_only'), "
            "EXISTS (SELECT 1 FROM information_schema.table_privileges WHERE grantee = current_user "
            "AND privilege_type IN ('INSERT','UPDATE','DELETE') "
            "AND table_schema NOT IN ('pg_catalog','information_schema'))",
        )[0]
        info: dict[str, Any] = {
            "user": row[0],
            "is_replica": bool(row[1]),
            "session_read_only": row[2] == "on",
            "role_has_write_grants": bool(row[3]),
        }
        if info["is_replica"]:
            ts = self._run_internal(database, "SELECT pg_last_xact_replay_timestamp()")[0][0]
            info["replica_replay_timestamp"] = ts.isoformat() if ts else None
        if info["role_has_write_grants"]:
            info["warning"] = "role has write grants; Snoop-DB still enforces read-only itself"
        return info
