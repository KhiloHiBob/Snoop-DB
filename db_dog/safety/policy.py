"""Query policy on top of the SQL guard (rules distilled from Bob's ``db-connection`` skill).

  * ``company_id`` first: a query touching a table that has a ``company_id`` column must filter it.
    Enforced for environments with ``company_id_enforced``, otherwise reported as a warning.
  * Tiered LIMIT: ``max_rows`` normally, ``star_rows`` for ``SELECT *``.
  * Schema / metadata queries are exempt from the company_id rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..config import Config, EnvConfig
from .sql_guard import GuardResult, guard_sql, has_company_id_predicate


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
) -> Prepared:
    limits = config.limits
    g = guard_sql(sql, dialect, int(limits["max_rows"]), int(limits["star_rows"]))
    if not g.ok:
        return Prepared(ok=False, reason=g.reason, guard=g)

    warnings: list[str] = []
    if g.kind in ("select",) and not g.is_metadata:
        scoped = {t.lower() for t in company_id_tables} - set(config.company_id_exempt_tables)
        needs = sorted(set(g.tables) & scoped)
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
