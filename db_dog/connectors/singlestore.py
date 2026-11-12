"""SingleStore / MySQL-protocol connector: SELECT, SHOW and DESCRIBE only."""

from __future__ import annotations

import time
from typing import Any

from ..config import Config, EnvConfig, resolve_secret
from ..preflight import require
from ..safety.policy import prepare_sql
from .base import QueryResult, hit_forced_limit, shape_rows
from .postgres import PolicyError

_SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys", "cluster"}


class SingleStoreConnector:
    store = "singlestore"
    dialect = "mysql"

    def __init__(self, config: Config, env: EnvConfig):
        self.config = config
        self.env = env
        self.cfg = env.stores["singlestore"]

    def _connect(self, database: str | None = None):
        import pymysql

        host, port = self.cfg.get("host", "localhost"), self.cfg.get("port", 3306)
        require(self.env, "singlestore", host, port)
        timeout_s = max(int(self.config.limits["statement_timeout_ms"]) // 1000, 1)
        conn = pymysql.connect(
            host=host,
            port=int(port),
            user=self.cfg.get("user") or None,
            password=resolve_secret(self.cfg, "password") or "",
            database=database or self.cfg.get("default_database") or None,
            connect_timeout=5,
            read_timeout=timeout_s,
            autocommit=False,
        )
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
        return conn

    def _internal(self, database: str | None, sql: str, params: tuple[Any, ...] = ()) -> list[tuple]:
        conn = self._connect(database)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or None)
                return list(cur.fetchall())
        finally:
            conn.rollback()
            conn.close()

    def list_databases(self) -> list[str]:
        rows = self._internal(None, "SHOW DATABASES")
        excluded = set(self.config.exclude_databases) | _SYSTEM_DBS
        return [r[0] for r in rows if r[0] not in excluded]

    def list_tables(self, database: str, like: str | None = None) -> list[dict[str, Any]]:
        rows = self._internal(
            database,
            "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name LIKE %s ORDER BY 1 LIMIT 1000",
            (database, f"%{like}%" if like else "%"),
        )
        return [{"table": r[0], "approx_rows": int(r[1] or 0)} for r in rows]

    def describe(self, database: str, table: str) -> dict[str, Any]:
        cols = self._internal(
            database,
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (database, table),
        )
        if not cols:
            raise ValueError(f"table {table} not found in database {database}")
        return {
            "table": f"{database}.{table}",
            "columns": [{"name": c[0], "type": c[1], "nullable": c[2] == "YES"} for c in cols],
            "has_company_id": any(c[0] == "company_id" for c in cols),
        }

    def company_id_tables(self, database: str) -> set[str]:
        rows = self._internal(
            database,
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE column_name = 'company_id' AND table_schema = %s",
            (database,),
        )
        return {r[0].lower() for r in rows}

    def query(self, database: str | None, sql: str) -> QueryResult:
        db = database or self.cfg.get("default_database") or None
        company_tables = self.company_id_tables(db) if db else set()
        prepared = prepare_sql(sql, self.dialect, self.config, self.env, company_tables)
        if not prepared.ok:
            raise PolicyError(prepared.reason)
        lim = self.config.limits
        started = time.monotonic()
        conn = self._connect(db)
        try:
            with conn.cursor() as cur:
                cur.execute(prepared.sql)
                columns = [d[0] for d in cur.description] if cur.description else []
                raw = list(cur.fetchmany(int(lim["max_rows"]) + 1)) if cur.description else []
        finally:
            conn.rollback()
            conn.close()
        max_rows = int(lim["max_rows"])
        rows, truncated_bytes = shape_rows(raw[:max_rows], int(lim["max_cell_chars"]), int(lim["max_result_bytes"]))
        notes = list(prepared.warnings)
        hit_limit = hit_forced_limit(prepared.guard, len(raw))
        if hit_limit:
            notes.append(f"row limit {prepared.guard.limit_applied} reached; more rows may exist")
        return QueryResult(
            columns=columns, rows=rows, row_count=len(rows),
            truncated=len(raw) > max_rows or truncated_bytes or hit_limit,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            executed=prepared.sql, notes=notes,
        )
