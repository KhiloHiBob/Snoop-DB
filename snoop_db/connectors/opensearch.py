"""OpenSearch / Elasticsearch connector: GET-style reads and ``_search`` / ``_count`` only."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import Config, EnvConfig, resolve_secret
from ..preflight import require
from .base import QueryResult, shape_rows
from .postgres import PolicyError

# (method, path regex) pairs that may be called. Everything else is refused.
ALLOWED = [
    ("GET", re.compile(r"^/_cat/[a-z_]+(/[\w.*,-]+)?$")),
    ("GET", re.compile(r"^/_cluster/health(/[\w.*,-]+)?$")),
    ("GET", re.compile(r"^/[\w.*,-]+/_(mapping|settings|count)$")),
    ("GET", re.compile(r"^/_(mapping|aliases)$")),
    ("GET", re.compile(r"^/[\w.*,-]+/_search$")),
    ("POST", re.compile(r"^/[\w.*,-]+/_(search|count)$")),
    ("GET", re.compile(r"^/[\w.*,-]+/_doc/[\w.:-]+$")),
]
MAX_SIZE = 200
SCRIPT_KEYS = {"script", "script_fields", "scripted_metric", "script_score"}


def _has_script(body: Any) -> bool:
    if isinstance(body, dict):
        return any(k in SCRIPT_KEYS or _has_script(v) for k, v in body.items())
    if isinstance(body, list):
        return any(_has_script(v) for v in body)
    return False


def validate_request(method: str, path: str, body: Any = None) -> tuple[str, str, Any]:
    method = (method or "GET").upper()
    path = "/" + (path or "").split("?")[0].lstrip("/")
    if not any(m == method and rx.match(path) for m, rx in ALLOWED):
        raise PolicyError(f"{method} {path} is not in the read-only allowlist")
    if body is not None:
        if not isinstance(body, dict):
            raise PolicyError("request body must be a JSON object")
        if _has_script(body):
            raise PolicyError("scripts are not allowed in queries")
        if path.endswith("/_search"):
            body = {**body, "size": min(int(body.get("size", 20)), MAX_SIZE)}
    return method, path, body


class OpenSearchConnector:
    store = "opensearch"

    def __init__(self, config: Config, env: EnvConfig):
        self.config = config
        self.env = env
        self.cfg = env.stores["opensearch"]

    def request(self, method: str, path: str, body: Any = None) -> QueryResult:
        method, path, body = validate_request(method, path, body)
        base = resolve_secret(self.cfg, "url") or "http://localhost:9200"
        u = urlparse(base)
        require(self.env, "opensearch", u.hostname, u.port or (443 if u.scheme == "https" else 80))
        auth = None
        if self.cfg.get("user"):
            auth = (self.cfg["user"], resolve_secret(self.cfg, "password") or "")
        started = time.monotonic()
        resp = httpx.request(
            method, base.rstrip("/") + path, json=body, auth=auth,
            timeout=int(self.config.limits["statement_timeout_ms"]) / 1000,
        )
        resp.raise_for_status()
        data = resp.json() if "json" in resp.headers.get("content-type", "") else {"text": resp.text}
        lim = self.config.limits
        if isinstance(data, dict) and "hits" in data:
            hits = data["hits"].get("hits", [])
            columns = ["_index", "_id", "_score", "_source"]
            raw = [[h.get("_index"), h.get("_id"), h.get("_score"), h.get("_source")] for h in hits]
        elif isinstance(data, list):
            columns = sorted({k for r in data if isinstance(r, dict) for k in r}) or ["value"]
            raw = [[r.get(c) if isinstance(r, dict) else r for c in columns] for r in data]
        else:
            columns, raw = ["field", "value"], [[k, v] for k, v in (data.items() if isinstance(data, dict) else [("value", data)])]
        rows, truncated = shape_rows(raw, int(lim["max_cell_chars"]), int(lim["max_result_bytes"]))
        return QueryResult(columns, rows, len(rows), truncated, int((time.monotonic() - started) * 1000),
                           f"{method} {path}")
