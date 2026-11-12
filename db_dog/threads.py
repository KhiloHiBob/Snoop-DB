"""Chat-thread persistence in a small Postgres (the ``threads-db`` service of docker-compose.yml).

Whole threads are upserted as JSON, so a page refresh restores the conversation. Result rows of
PRODUCTION environments are never stored (``strip_production_rows``): re-ask to see them again.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

DEFAULT_DSN = "postgresql://dbdog:dbdog@127.0.0.1:55433/dog_threads"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_threads (
    id          text PRIMARY KEY,
    title       text NOT NULL,
    turns       jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
)
"""


class ThreadStoreUnavailable(RuntimeError):
    pass


def strip_production_rows(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop result rows (keep summary, environment and executed SQL) for production answers."""
    out = []
    for turn in turns:
        ans = turn.get("answer")
        if isinstance(ans, dict) and ans.get("production"):
            ans = {**ans, "results": [], "warnings": [*(ans.get("warnings") or []),
                   "Production result rows are not stored. Ask again to see them."]}
            turn = {**turn, "answer": ans}
        out.append(turn)
    return out


class ThreadStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("DB_DOG_THREADS_DSN") or DEFAULT_DSN
        self._ready = False

    def _conn(self):
        import psycopg
        try:
            conn = psycopg.connect(self.dsn, connect_timeout=3, autocommit=True)
            if not self._ready:
                conn.execute(SCHEMA)
                self._ready = True
            return conn
        except Exception as e:
            raise ThreadStoreUnavailable(
                "thread store is not reachable; start it with `docker compose up -d threads-db`") from e

    def list(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT id, title, turns, updated_at FROM chat_threads ORDER BY created_at").fetchall()
        return [{"id": r[0], "title": r[1], "turns": r[2], "updated_at": r[3].isoformat()} for r in rows]

    def save(self, thread_id: str, title: str, turns: list[dict[str, Any]]) -> None:
        if not ID_RE.match(thread_id):
            raise ValueError("invalid thread id")
        with self._conn() as c:
            c.execute(
                "INSERT INTO chat_threads (id, title, turns) VALUES (%s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, turns = EXCLUDED.turns, updated_at = now()",
                (thread_id, title[:200], json.dumps(strip_production_rows(turns))))

    def delete(self, thread_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM chat_threads WHERE id = %s", (thread_id,))
