"""Shared connector types and result shaping."""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import ipaddress
import json
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    elapsed_ms: int = 0
    executed: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_ms": self.elapsed_ms,
            "executed": self.executed,
            "notes": self.notes,
        }


def hit_forced_limit(guard: Any, fetched: int) -> bool:
    """True when we imposed the LIMIT and the database returned that many rows (so there may be more)."""
    return bool(
        guard is not None and guard.kind == "select" and guard.limit_forced
        and guard.limit_applied is not None and fetched >= guard.limit_applied
    )


def to_jsonable(value: Any, max_cell_chars: int = 2000) -> Any:
    """Make a DB value JSON-safe and bound its size."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, (uuid.UUID, ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return f"<{len(raw)} bytes: {base64.b64encode(raw[:24]).decode()}...>"
    if isinstance(value, (list, tuple, set, frozenset)):
        value = [to_jsonable(v, max_cell_chars) for v in value]
    elif isinstance(value, dict):
        value = {str(k): to_jsonable(v, max_cell_chars) for k, v in value.items()}
    else:
        value = str(value)
    # Bound very large nested / text values.
    if isinstance(value, str):
        return value if len(value) <= max_cell_chars else value[:max_cell_chars] + f"... [{len(value)} chars]"
    text = json.dumps(value, default=str)
    if len(text) <= max_cell_chars:
        return value
    return text[:max_cell_chars] + f"... [{len(text)} chars]"


def shape_rows(
    raw_rows: list[tuple[Any, ...]] | list[list[Any]],
    max_cell_chars: int,
    max_result_bytes: int,
) -> tuple[list[list[Any]], bool]:
    """Convert rows to JSON-safe lists, stopping once the byte budget is exhausted."""
    out: list[list[Any]] = []
    used = 0
    for r in raw_rows:
        row = [to_jsonable(v, max_cell_chars) for v in r]
        used += len(json.dumps(row, default=str))
        if used > max_result_bytes:
            return out, True
        out.append(row)
    return out, False
