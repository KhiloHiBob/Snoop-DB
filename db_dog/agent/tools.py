"""Tool schemas and execution. Every tool is read-only and goes through a connector's guard."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..config import Config, EnvConfig
from ..connectors import ConnectorHub
from ..connectors.postgres import PolicyError
from ..preflight import PreflightError
from ..safety.sql_guard import guard_sql

LLM_ROW_PREVIEW = 50


def tool_schemas(stores: list[str]) -> list[dict[str, Any]]:
    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
    s = {"type": "string"}
    show = {"type": "boolean", "description": "Show this result to the user as a table (default false)."}
    store_prop = {"type": "string", "enum": [x for x in ("postgres", "singlestore", "mongo") if x in stores],
                  "description": "Which store (default postgres)."}
    tools: list[dict[str, Any]] = []
    sql_stores = [x for x in ("postgres", "singlestore") if x in stores]
    if sql_stores:
        sql_store = {**store_prop, "enum": sql_stores}
        tools += [
            {"name": "list_databases", "description": "List databases/schemas on a store.",
             "input_schema": obj({"store": store_prop})},
            {"name": "list_tables", "description": "List tables in a database (with approximate row counts).",
             "input_schema": obj({"database": s, "like": {**s, "description": "substring filter"},
                                  "store": sql_store, "show_to_user": show}, ["database"])},
            {"name": "describe_table", "description": "Columns, types and indexes of a table, and whether it has company_id.",
             "input_schema": obj({"database": s, "table": s, "store": sql_store, "show_to_user": show},
                                 ["database", "table"])},
            {"name": "run_sql",
             "description": "Run ONE read-only SELECT (or plain EXPLAIN). A LIMIT is applied automatically. "
                            "Result is shown to the user as a table.",
             "input_schema": obj({"database": s, "sql": s, "title": {**s, "description": "short table title"},
                                  "store": sql_store}, ["database", "sql"])},
        ]
    if "postgres" in stores:
        tools.append({
            "name": "fan_out_query",
            "description": "Run the same read-only SELECT on many Postgres databases of this server "
                           "(result gains a _database column). Use to find where a table/data lives.",
            "input_schema": obj({"sql": s, "database_like": {**s, "description": "only databases containing this text"},
                                 "title": s}, ["sql"]),
        })
    if "redis" in stores:
        tools.append({"name": "redis_read", "description": "Run ONE read-only Redis command (SCAN, not KEYS).",
                      "input_schema": obj({"command": s, "title": s}, ["command"])})
    if "mongo" in stores:
        tools += [
            {"name": "mongo_list_collections", "description": "List collections of a Mongo database.",
             "input_schema": obj({"database": s}, ["database"])},
            {"name": "mongo_find", "description": "Find documents (filter is a JSON object).",
             "input_schema": obj({"database": s, "collection": s, "filter": {"type": "object"},
                                  "projection": {"type": "object"}, "sort": {"type": "object"},
                                  "limit": {"type": "integer"}, "title": s}, ["database", "collection"])},
            {"name": "mongo_aggregate", "description": "Run a read-only aggregation pipeline.",
             "input_schema": obj({"database": s, "collection": s, "pipeline": {"type": "array"}, "title": s},
                                 ["database", "collection", "pipeline"])},
        ]
    if "opensearch" in stores:
        tools.append({"name": "opensearch_search",
                      "description": "Read-only OpenSearch request: GET /_cat/indices, /<index>/_mapping, POST /<index>/_search ...",
                      "input_schema": obj({"method": {"type": "string", "enum": ["GET", "POST"]}, "path": s,
                                           "body": {"type": "object"}, "title": s}, ["method", "path"])})
    return tools


@dataclass
class ToolRuntime:
    config: Config
    hub: ConnectorHub
    env: EnvConfig
    rows_visible_to_llm: bool
    results: list[dict[str, Any]] = field(default_factory=list)   # tables shown to the user
    steps: list[dict[str, Any]] = field(default_factory=list)     # transparent log of every tool call
    warnings: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- helpers
    def _record(self, role: str, title: str, store: str, database: str | None, res: dict[str, Any]) -> None:
        self.results.append({"role": role, "title": title, "store": store, "database": database, **res})

    def _llm_view(self, res: dict[str, Any]) -> dict[str, Any]:
        view = {"columns": res["columns"], "row_count": res["row_count"], "truncated": res["truncated"]}
        if res.get("notes"):
            view["notes"] = res["notes"]
        if self.rows_visible_to_llm:
            view["rows"] = res["rows"][:LLM_ROW_PREVIEW]
            if res["row_count"] > LLM_ROW_PREVIEW:
                view["note"] = f"only the first {LLM_ROW_PREVIEW} rows are shown to you"
        else:
            view["rows"] = "withheld for privacy; the user sees the full table"
        return view

    # ---------------------------------------------------------------- dispatch
    def call(self, name: str, args: dict[str, Any]) -> str:
        step: dict[str, Any] = {"tool": name, "input": args, "status": "ok"}
        try:
            out = self._dispatch(name, args)
        except (PolicyError, PreflightError, KeyError, ValueError) as e:
            step.update(status="rejected" if isinstance(e, PolicyError) else "error", detail=str(e))
            out = {"error": str(e)}
        except Exception as e:  # driver/network errors: report, never crash the loop
            step.update(status="error", detail=f"{type(e).__name__}: {str(e)[:300]}")
            out = {"error": step["detail"]}
        self.steps.append(step)
        return json.dumps(out, default=str)

    def _dispatch(self, name: str, a: dict[str, Any]) -> Any:
        store = a.get("store") or "postgres"
        if name == "list_databases":
            dbs = self.hub.get(self.env.name, store).list_databases()
            return {"databases": dbs}
        if name == "list_tables":
            conn = self.hub.get(self.env.name, store)
            tables = conn.list_tables(a["database"], like=a.get("like"))
            if a.get("show_to_user"):
                rows = [[t["table"], t["approx_rows"]] for t in tables]
                self._record("answer", f"Tables in {a['database']}", store, a["database"],
                             {"columns": ["table", "approx_rows"], "rows": rows, "row_count": len(rows),
                              "truncated": False, "elapsed_ms": 0, "executed": "catalog lookup", "notes": []})
            return {"tables": tables}
        if name == "describe_table":
            conn = self.hub.get(self.env.name, store)
            d = conn.describe(a["database"], a["table"])
            if a.get("show_to_user"):
                rows = [[c["name"], c["type"], c["nullable"]] for c in d["columns"]]
                self._record("answer", f"Columns of {d['table']}", store, a["database"],
                             {"columns": ["column", "type", "nullable"], "rows": rows, "row_count": len(rows),
                              "truncated": False, "elapsed_ms": 0, "executed": "catalog lookup", "notes": []})
            return d
        if name == "run_sql":
            res = self.hub.get(self.env.name, store).query(a.get("database"), a["sql"]).to_dict()
            self._record("answer", a.get("title") or "Result", store, a.get("database"), res)
            return self._llm_view(res)
        if name == "fan_out_query":
            return self._fan_out(a)
        if name == "redis_read":
            res = self.hub.get(self.env.name, "redis").run(a["command"]).to_dict()
            self._record("answer", a.get("title") or "Redis", "redis", None, res)
            return self._llm_view(res)
        if name == "mongo_list_collections":
            return {"collections": self.hub.get(self.env.name, "mongo").list_collections(a["database"])}
        if name == "mongo_find":
            res = self.hub.get(self.env.name, "mongo").find(
                a["database"], a["collection"], a.get("filter"), a.get("projection"), a.get("sort"), a.get("limit", 50)
            ).to_dict()
            self._record("answer", a.get("title") or a["collection"], "mongo", a["database"], res)
            return self._llm_view(res)
        if name == "mongo_aggregate":
            res = self.hub.get(self.env.name, "mongo").aggregate(a["database"], a["collection"], a["pipeline"]).to_dict()
            self._record("answer", a.get("title") or a["collection"], "mongo", a["database"], res)
            return self._llm_view(res)
        if name == "opensearch_search":
            res = self.hub.get(self.env.name, "opensearch").request(a["method"], a["path"], a.get("body")).to_dict()
            self._record("answer", a.get("title") or "OpenSearch", "opensearch", None, res)
            return self._llm_view(res)
        raise ValueError(f"unknown tool '{name}'")

    def _fan_out(self, a: dict[str, Any]) -> dict[str, Any]:
        conn = self.hub.get(self.env.name, "postgres")
        dbs = conn.list_databases()
        if a.get("database_like"):
            dbs = [d for d in dbs if a["database_like"].lower() in d.lower()]
        # Catalog-only queries (information_schema / pg_catalog) are cheap, so they search every database.
        metadata_only = guard_sql(a["sql"], "postgres").is_metadata
        cap = int(self.config.limits["fan_out_max_metadata_databases" if metadata_only else "fan_out_max_databases"])
        notes: list[str] = []
        if len(dbs) > cap:
            notes.append(f"ONLY the first {cap} of {len(dbs)} databases were queried (alphabetical), so the result "
                         "is incomplete; narrow with database_like and run again")
            dbs = dbs[:cap]
        columns: list[str] = []
        rows: list[list[Any]] = []
        max_rows = int(self.config.limits["max_rows"])
        executed = ""
        for db in dbs:
            try:
                r = conn.query(db, a["sql"])
            except PolicyError:
                raise  # the statement itself is bad: stop, don't repeat it on every database
            except Exception as e:  # per-database problems (permissions, missing table) are skipped
                notes.append(f"{db}: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
                continue
            executed = r.executed
            columns = columns or ["_database", *r.columns]
            rows += [[db, *row] for row in r.rows]
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        res = {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
               "elapsed_ms": 0, "executed": executed, "notes": notes + [f"queried {len(dbs)} database(s)"]}
        if rows:  # an empty table is noise for the user; the agent still gets the notes below
            self._record("answer", a.get("title") or "Across databases", "postgres", None, res)
        return self._llm_view(res)
