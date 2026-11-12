"""Redis connector: an allowlist of read-only commands. ``KEYS`` is refused; use ``SCAN``."""

from __future__ import annotations

import shlex
import time
from typing import Any

from ..config import Config, EnvConfig, resolve_secret
from ..preflight import require
from .base import QueryResult, to_jsonable
from .postgres import PolicyError

ALLOWED_COMMANDS = {
    "GET", "MGET", "EXISTS", "TYPE", "TTL", "PTTL", "STRLEN", "GETRANGE",
    "HGET", "HGETALL", "HLEN", "HKEYS", "HMGET", "HEXISTS", "HSTRLEN",
    "LLEN", "LRANGE", "LINDEX",
    "SCARD", "SMEMBERS", "SISMEMBER", "SRANDMEMBER",
    "ZCARD", "ZRANGE", "ZRANGEBYSCORE", "ZSCORE", "ZRANK", "ZCOUNT",
    "SCAN", "SSCAN", "HSCAN", "ZSCAN",
    "DBSIZE", "INFO", "MEMORY", "OBJECT", "XLEN", "XRANGE", "XINFO", "PING", "TIME",
}
# Sub-commands allowed for the multi-word commands above.
ALLOWED_SUBCOMMANDS = {
    "MEMORY": {"USAGE"},
    "OBJECT": {"ENCODING", "FREQ", "IDLETIME", "REFCOUNT"},
    "XINFO": {"STREAM", "GROUPS"},
}
MAX_SCAN_COUNT = 1000
MAX_ITEMS = 500


def validate_command(command: str) -> list[str]:
    """Return the tokenised command, or raise ``PolicyError``."""
    try:
        parts = shlex.split(command)
    except ValueError as e:
        raise PolicyError(f"could not parse the Redis command: {e}") from e
    if not parts:
        raise PolicyError("empty Redis command")
    name = parts[0].upper()
    if name == "KEYS":
        raise PolicyError("KEYS blocks the server; use SCAN <cursor> MATCH <pattern> COUNT <n> instead")
    if name not in ALLOWED_COMMANDS:
        raise PolicyError(f"Redis command {name} is not in the read-only allowlist")
    if name in ALLOWED_SUBCOMMANDS:
        sub = parts[1].upper() if len(parts) > 1 else ""
        if sub not in ALLOWED_SUBCOMMANDS[name]:
            raise PolicyError(f"{name} {sub} is not allowed")
    if name == "INFO" and len(parts) > 2:
        raise PolicyError("INFO takes at most one section")
    parts[0] = name
    if name in ("SCAN", "SSCAN", "HSCAN", "ZSCAN"):
        upper = [p.upper() for p in parts]
        if "COUNT" in upper:
            i = upper.index("COUNT")
            try:
                parts[i + 1] = str(min(int(parts[i + 1]), MAX_SCAN_COUNT))
            except (IndexError, ValueError) as e:
                raise PolicyError("COUNT needs an integer") from e
        else:
            parts += ["COUNT", "100"]
    return parts


class RedisConnector:
    store = "redis"

    def __init__(self, config: Config, env: EnvConfig):
        self.config = config
        self.env = env
        self.cfg = env.stores["redis"]

    def _client(self):
        import redis

        url = resolve_secret(self.cfg, "url")
        if url:
            from urllib.parse import urlparse

            u = urlparse(url)
            require(self.env, "redis", u.hostname, u.port or 6379)
            return redis.Redis.from_url(url, socket_timeout=5, decode_responses=True)
        host, port = self.cfg.get("host", "localhost"), self.cfg.get("port", 6379)
        require(self.env, "redis", host, port)
        return redis.Redis(
            host=host, port=int(port), password=resolve_secret(self.cfg, "password"),
            socket_timeout=5, decode_responses=True,
        )

    def run(self, command: str) -> QueryResult:
        parts = validate_command(command)
        started = time.monotonic()
        client = self._client()
        try:
            raw = client.execute_command(*parts)
        finally:
            client.close()
        cell = int(self.config.limits["max_cell_chars"])
        notes: list[str] = []
        truncated = False
        if isinstance(raw, dict):
            items = list(raw.items())
            truncated = len(items) > MAX_ITEMS
            rows = [[to_jsonable(k, cell), to_jsonable(v, cell)] for k, v in items[:MAX_ITEMS]]
            columns = ["field", "value"]
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
            truncated = len(items) > MAX_ITEMS
            rows = [[to_jsonable(i, cell)] for i in items[:MAX_ITEMS]]
            columns = ["value"]
        else:
            rows, columns = [[to_jsonable(raw, cell)]], ["value"]
        if truncated:
            notes.append(f"result truncated to {MAX_ITEMS} items")
        return QueryResult(
            columns=columns, rows=rows, row_count=len(rows), truncated=truncated,
            elapsed_ms=int((time.monotonic() - started) * 1000), executed=" ".join(parts), notes=notes,
        )
