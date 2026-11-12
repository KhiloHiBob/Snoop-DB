"""MongoDB connector: find / count / aggregate with an operator allowlist."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from ..config import Config, EnvConfig, resolve_secret
from ..preflight import require
from .base import QueryResult, shape_rows, to_jsonable
from .postgres import PolicyError

ALLOWED_STAGES = {
    "$match", "$project", "$group", "$sort", "$limit", "$skip", "$count", "$unwind", "$addFields",
    "$set", "$unset", "$lookup", "$facet", "$sortByCount", "$replaceRoot", "$replaceWith", "$sample",
}
FORBIDDEN_KEYS = {"$out", "$merge", "$where", "$function", "$accumulator"}
MAX_DOCS = 500


def _scan(value: Any) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k) in FORBIDDEN_KEYS:
                raise PolicyError(f"operator {k} is not allowed")
            _scan(v)
    elif isinstance(value, list):
        for v in value:
            _scan(v)


def validate_filter(flt: Any) -> dict[str, Any]:
    if flt is None:
        return {}
    if not isinstance(flt, dict):
        raise PolicyError("filter must be a JSON object")
    _scan(flt)
    return flt


def validate_pipeline(pipeline: Any) -> list[dict[str, Any]]:
    if not isinstance(pipeline, list) or not pipeline:
        raise PolicyError("pipeline must be a non-empty list of stages")
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise PolicyError("each pipeline stage must be an object with exactly one operator")
        op = next(iter(stage))
        if op not in ALLOWED_STAGES:
            raise PolicyError(f"pipeline stage {op} is not allowed")
        _scan(stage)
    return pipeline


class MongoConnector:
    store = "mongo"

    def __init__(self, config: Config, env: EnvConfig):
        self.config = config
        self.env = env
        self.cfg = env.stores["mongo"]

    def _client(self):
        from pymongo import MongoClient

        uri = resolve_secret(self.cfg, "uri") or "mongodb://localhost:27017/"
        u = urlparse(uri)
        require(self.env, "mongo", u.hostname or "localhost", u.port or 27017)
        return MongoClient(uri, serverSelectionTimeoutMS=5000,
                           socketTimeoutMS=int(self.config.limits["statement_timeout_ms"]))

    def list_databases(self) -> list[str]:
        c = self._client()
        try:
            return [d for d in c.list_database_names() if d not in ("admin", "local", "config")]
        finally:
            c.close()

    def list_collections(self, database: str) -> list[str]:
        c = self._client()
        try:
            return sorted(c[database].list_collection_names())
        finally:
            c.close()

    def _result(self, docs: list[dict[str, Any]], executed: str, started: float, truncated: bool) -> QueryResult:
        lim = self.config.limits
        columns: list[str] = []
        for d in docs:
            for k in d:
                if k not in columns:
                    columns.append(k)
        raw = [[d.get(k) for k in columns] for d in docs]
        rows, trunc_bytes = shape_rows(raw, int(lim["max_cell_chars"]), int(lim["max_result_bytes"]))
        return QueryResult(
            columns=columns, rows=rows, row_count=len(rows), truncated=truncated or trunc_bytes,
            elapsed_ms=int((time.monotonic() - started) * 1000), executed=executed,
        )

    def find(self, database: str, collection: str, flt: Any = None, projection: Any = None,
             sort: Any = None, limit: int = 50) -> QueryResult:
        flt = validate_filter(flt)
        if projection is not None:
            validate_filter(projection)
        limit = max(1, min(int(limit or 50), MAX_DOCS))
        executed = f"{database}.{collection}.find({json.dumps(flt, default=str)}) limit {limit}"
        started = time.monotonic()
        c = self._client()
        try:
            cur = c[database][collection].find(flt, projection).max_time_ms(int(self.config.limits["statement_timeout_ms"]))
            if isinstance(sort, dict) and sort:
                cur = cur.sort(list(sort.items()))
            docs = [dict(d) for d in cur.limit(limit + 1)]
        finally:
            c.close()
        truncated = len(docs) > limit
        return self._result(docs[:limit], executed, started, truncated)

    def count(self, database: str, collection: str, flt: Any = None) -> QueryResult:
        flt = validate_filter(flt)
        started = time.monotonic()
        c = self._client()
        try:
            n = c[database][collection].count_documents(
                flt, maxTimeMS=int(self.config.limits["statement_timeout_ms"]))
        finally:
            c.close()
        return QueryResult(["count"], [[n]], 1, False, int((time.monotonic() - started) * 1000),
                           f"{database}.{collection}.count({json.dumps(flt, default=str)})")

    def aggregate(self, database: str, collection: str, pipeline: Any) -> QueryResult:
        pipeline = validate_pipeline(pipeline)
        if not any("$limit" in s or "$count" in s or "$group" in s for s in pipeline):
            pipeline = pipeline + [{"$limit": MAX_DOCS + 1}]
        started = time.monotonic()
        c = self._client()
        try:
            docs = [dict(d) for d in c[database][collection].aggregate(
                pipeline, maxTimeMS=int(self.config.limits["statement_timeout_ms"]), allowDiskUse=False)]
        finally:
            c.close()
        truncated = len(docs) > MAX_DOCS
        executed = f"{database}.{collection}.aggregate({json.dumps(pipeline, default=str)})"
        return self._result(docs[:MAX_DOCS], executed, started, truncated)


__all__ = ["MongoConnector", "validate_filter", "validate_pipeline", "to_jsonable"]
