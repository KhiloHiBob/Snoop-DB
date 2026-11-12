from __future__ import annotations

from typing import Any

import pytest
import yaml

from db_dog.config import Config, load_config
from db_dog.connectors.base import QueryResult
from db_dog.connectors.postgres import PolicyError
from db_dog.agent.llm import LLMResponse
from db_dog.safety.policy import prepare_sql

CONFIG = {
    "environments": {
        "local": {"keywords": ["local", "demo"], "postgres": {"host": "localhost", "port": 1, "default_database": "demo"},
                  "redis": {"host": "localhost"}},
        "staging": {"keywords": ["staging", "stage"], "postgres": {"host": "localhost", "port": 2, "default_database": "demo"}},
        "sandbox": {"keywords": ["sandbox"], "postgres": {"host": "localhost", "port": 3, "default_database": "demo"}},
        "prod": {"production": True, "keywords": ["prod", "production"],
                 "postgres": {"host": "localhost", "port": 4, "default_database": "demo"}},
        "payroll-prod": {"production": True, "keywords": ["payroll prod", "payroll-prod"],
                         "postgres": {"host": "localhost", "port": 5, "default_database": "demo"}},
    }
}


@pytest.fixture
def config(tmp_path) -> Config:
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(CONFIG))
    return load_config(p)


class FakePostgres:
    """Stands in for PostgresConnector; applies the real policy but never touches a database."""

    def __init__(self, config: Config, env):
        self.config, self.env = config, env
        self.queries: list[str] = []

    def list_databases(self) -> list[str]:
        return ["demo", "other"]

    def list_tables(self, database, like=None, schema="public"):
        return [{"table": "employees", "approx_rows": 120}, {"table": "companies", "approx_rows": 3}]

    def describe(self, database, table, schema="public"):
        return {"table": f"public.{table}", "columns": [{"name": "id", "type": "bigint", "nullable": False}],
                "indexes": [], "has_company_id": table == "employees"}

    def company_id_tables(self, database):
        return {"employees", "audit_log"}

    def big_tables(self, database):
        return {"audit_log": 5_000_000}

    def query(self, database, sql) -> QueryResult:
        prepared = prepare_sql(sql, "postgres", self.config, self.env, self.company_id_tables(database),
                               self.big_tables(database))
        if not prepared.ok:
            raise PolicyError(prepared.reason)
        self.queries.append(prepared.sql)
        return QueryResult(["department", "n"], [["Engineering", 24], ["Sales", 24]], 2, False, 3, prepared.sql,
                           list(prepared.warnings))

    def health(self, database=None):
        return {"user": "dbdog", "is_replica": False, "session_read_only": True, "role_has_write_grants": False}


class FakeHub:
    def __init__(self, config: Config):
        self.config = config
        self.conns: dict[str, FakePostgres] = {}

    def stores_for(self, env_name):
        return list(self.config.environments[env_name].stores)

    def get(self, env_name, store):
        if store != "postgres":
            raise KeyError(f"environment '{env_name}' has no '{store}' store configured")
        env = self.config.environments[env_name]
        return self.conns.setdefault(env_name, FakePostgres(self.config, env))


class ScriptedLLM:
    """Replays a fixed list of responses and records what it was sent."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        return self.responses.pop(0)


def tool_call(name: str, args: dict[str, Any], id_: str = "t1") -> LLMResponse:
    return LLMResponse("tool_use", [{"type": "tool_use", "id": id_, "name": name, "input": args}])


def final(text: str) -> LLMResponse:
    return LLMResponse("end_turn", [{"type": "text", "text": text}])
