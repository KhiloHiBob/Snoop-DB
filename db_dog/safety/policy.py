"""Query policy on top of the SQL guard (rules distilled from Bob's ``db-connection`` skill).

  * ``company_id`` first: a query touching a table that has a ``company_id`` column must filter it.
    Enforced for environments with ``company_id_enforced``, otherwise reported as a warning.
  * Tiered LIMIT: ``max_rows`` normally, ``star_rows`` for ``SELECT *``.
  * Big tables (``limits.big_table_rows``, default 100k): a company_id predicate is ALWAYS required, in
    every environment, and the agent must ask the user for the company instead of guessing.
  * Schema / metadata queries are exempt from the company_id rule.
  * Platform-wide aggregates (``limits.allow_global_aggregates``, default on): a pure aggregate over one table,
    e.g. ``SELECT count(*) FROM employee``, is allowed without company_id (it returns no rows or per-tenant data).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..config import Config, EnvConfig
from .sql_guard import GuardResult, guard_sql, has_company_id_predicate, is_global_aggregate


@dataclass
class Prepared:
    ok: bool
    sql: str = ""
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    guard: GuardResult | None = None


def prepare_sql(
    sql: str,
    dialect: str,
    config: Config,
    env: EnvConfig,
    company_id_tables: Iterable[str] = (),
    big_tables: Mapping[str, int] | None = None,
) -> Prepared:
    limits = config.limits
    g = guard_sql(sql, dialect, int(limits["max_rows"]), int(limits["star_rows"]))
    if not g.ok:
        return Prepared(ok=False, reason=g.reason, guard=g)

    warnings: list[str] = []
    if g.kind in ("select",) and not g.is_metadata and limits.get("allow_global_aggregates", True) \
            and is_global_aggregate(g.ast) and not has_company_id_predicate(g.ast):
        return Prepared(ok=True, sql=g.sql, warnings=["Platform-wide aggregate across ALL companies (no company_id filter)."], guard=g)
    if g.kind in ("select",) and not g.is_metadata:
        scoped = {t.lower() for t in company_id_tables} - set(config.company_id_exempt_tables)
        needs = sorted(set(g.tables) & scoped)
        # Big tables: ALWAYS require a company_id filter, in every environment (never scan them blind).
        big = {t.lower(): n for t, n in (big_tables or {}).items()}
        big_needs = sorted(t for t in needs if t in big)
        if big_needs and not has_company_id_predicate(g.ast):
            sizes = ", ".join(f"{t} (~{big[t]:,} rows)" for t in big_needs)
            return Prepared(
                ok=False,
                reason=(
                    f"big table(s) {sizes} must be queried with a `company_id = <value>` predicate in every "
                    "environment. Do NOT guess a company id: ask the user which company to use, then retry."
                ),
                guard=g,
            )
        if needs and not has_company_id_predicate(g.ast):
            msg = (
                f"table(s) {', '.join(needs)} have a company_id column but the query has no "
                "`company_id = <value>` (or IN) predicate"
            )
            if env.company_id_enforced:
                return Prepared(
                    ok=False,
                    reason=msg + f"; this is required in '{env.name}'. Add a company_id filter.",
                    guard=g,
                )
            warnings.append(msg + " (not enforced in this environment)")

    return Prepared(ok=True, sql=g.sql, warnings=warnings, guard=g)
