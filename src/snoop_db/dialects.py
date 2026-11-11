from contextlib import contextmanager
from typing import Protocol

import psycopg
from psycopg import sql

from snoop_db.config import Settings


class DatabasePolicyError(ValueError):
    pass


class Dialect(Protocol):
    sqlglot_name: str

    def read_session(self, settings: Settings): ...
    def discover(self, settings: Settings) -> dict: ...
    def bind(self, query: str, parameters: dict) -> tuple[str, dict]: ...


class PostgresDialect:
    sqlglot_name = "postgres"

    @contextmanager
    def read_session(self, settings):
        with psycopg.connect(settings.database_url, autocommit=True, connect_timeout=5) as conn:
            with conn.transaction(force_rollback=True):
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL search_path = pg_catalog")
                conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(
                    sql.Literal(settings.statement_timeout_ms)))
                conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(
                    sql.Literal(settings.statement_timeout_ms)))
                self._check_role(conn, settings.allowed_relations)
                yield conn

    def _check_role(self, conn, allowed):
        role = conn.execute("""
            SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
            FROM pg_roles WHERE rolname = current_user
        """).fetchone()
        memberships = conn.execute("""
            SELECT 1 FROM pg_auth_members m
            JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = current_user LIMIT 1
        """).fetchone()
        elevated = conn.execute("""
            SELECT has_database_privilege(current_database(), 'CREATE,TEMP')
                OR EXISTS (
                    SELECT 1 FROM pg_namespace
                    WHERE has_schema_privilege(oid, 'CREATE')
                )
        """).fetchone()[0]
        if not role or role[0] or memberships or elevated:
            raise DatabasePolicyError("Use a dedicated SELECT-only role without memberships or CREATE/TEMP")
        relations = conn.execute("""
            SELECT n.nspname || '.' || c.relname,
                   has_table_privilege(c.oid, 'SELECT')
                       OR has_any_column_privilege(c.oid, 'SELECT'),
                   has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                       OR has_any_column_privilege(c.oid, 'INSERT,UPDATE,REFERENCES')
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname NOT LIKE 'pg_toast%%'
        """).fetchall()
        if any(writable or (readable and name not in allowed)
               for name, readable, writable in relations):
            raise DatabasePolicyError("Database role has privileges beyond the explicit SELECT allowlist")
        unsafe_objects = conn.execute("""
            SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%%'
                  AND CASE WHEN c.relkind = 'S'
                      THEN has_sequence_privilege(c.oid, 'USAGE,UPDATE') ELSE false END
            ) OR EXISTS (
                SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND has_function_privilege(p.oid, 'EXECUTE')
            )
        """).fetchone()[0]
        if unsafe_objects:
            raise DatabasePolicyError("Revoke sequence writes and execution of user-defined routines")

    def discover(self, settings):
        from snoop_db.schema import discover_postgres

        return discover_postgres(self, settings)

    def bind(self, query, parameters):
        # Escape modulo operators, then restore only validator-generated bindings.
        import re

        query = query.replace("%", "%%")
        return re.sub(r"%%\((p[0-9]+)\)s", r"%(\1)s", query), parameters


DIALECTS: dict[str, Dialect] = {"postgres": PostgresDialect()}


def get_dialect(name: str) -> Dialect:
    if name not in DIALECTS:
        raise ValueError("Unsupported database dialect; register a complete adapter first")
    return DIALECTS[name]
