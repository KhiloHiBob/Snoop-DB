import math
import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.optimizer.qualify import qualify


# SQLGlot's unsupported-command warnings include raw SQL, potentially with values.
logging.getLogger("sqlglot").setLevel(logging.CRITICAL)


class UnsafeQuery(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    parameters: dict


# A deliberately small grammar: new syntax requires an explicit policy review.
SAFE_NODES = {
    exp.Select, exp.From, exp.Table, exp.Column, exp.Identifier, exp.Star,
    exp.Alias, exp.TableAlias, exp.Join, exp.Where, exp.Group, exp.Having,
    exp.Order, exp.Ordered, exp.Limit, exp.Offset, exp.Distinct,
    exp.With, exp.CTE, exp.Subquery, exp.Union, exp.Intersect, exp.Except,
    exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.Is, exp.In, exp.Between, exp.Like, exp.ILike, exp.Exists,
    exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg, exp.Paren,
    exp.Case, exp.If, exp.Literal, exp.Boolean, exp.Null, exp.Placeholder,
    exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Lower, exp.Upper,
    exp.Length, exp.Coalesce, exp.Nullif, exp.Abs, exp.Round,
    exp.Cast, exp.DataType, exp.DataTypeParam,
}
SAFE_CAST_TYPES = {"INT", "BIGINT", "SMALLINT", "DECIMAL", "DOUBLE", "FLOAT",
                   "BOOLEAN", "TEXT", "VARCHAR", "CHAR", "DATE", "TIMESTAMP", "TIMESTAMPTZ"}


def validate_sql(query: str, parameters: dict, schema: dict, dialect="postgres") -> ValidatedQuery:
    if not isinstance(query, str) or not query.strip() or len(query) > 20000:
        raise UnsafeQuery("Provide one SELECT statement of at most 20,000 characters")
    if not isinstance(parameters, dict) or any(
        not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", key)
        or not (value is None or type(value) in {str, int, float, bool})
        or (type(value) is float and not math.isfinite(value))
        or (isinstance(value, str) and len(value) > 10000)
        for key, value in parameters.items()
    ):
        raise UnsafeQuery("Parameters must be named JSON scalar values")
    try:
        statements = sqlglot.parse(query, read=dialect)
    except (SqlglotError, RecursionError) as exc:
        raise UnsafeQuery("SQL could not be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
        raise UnsafeQuery("Only one read-only SELECT (including safe CTEs) is permitted")
    tree = statements[0]
    for node in tree.walk():
        if type(node) not in SAFE_NODES:
            raise UnsafeQuery(f"SQL construct is not approved: {type(node).__name__}")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise UnsafeQuery("Recursive CTEs are not supported")
        if isinstance(node, exp.DataType) and node.this.value not in SAFE_CAST_TYPES:
            raise UnsafeQuery("Cast type is not approved")
        if isinstance(node, exp.Identifier) and "%" in node.name:
            raise UnsafeQuery("Percent signs are not supported in identifiers")
        if isinstance(node, exp.Column) and (node.db or node.catalog):
            raise UnsafeQuery("Use table aliases for column qualification")
        if isinstance(node, (exp.Group, exp.Ordered)):
            expressions = node.expressions if isinstance(node, exp.Group) else [node.this]
            if any(isinstance(item, (exp.Literal, exp.Placeholder)) for item in expressions):
                raise UnsafeQuery("Use named expressions instead of positional GROUP BY/ORDER BY")
    try:
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, Scope):
                    continue
                if (not isinstance(source, exp.Table) or source.catalog
                        or not source.db or f"{source.db}.{source.name}" not in schema
                        or source.args.get("pivots")):
                    raise UnsafeQuery("Every base table/view must be explicitly schema-qualified and allowed")
        known_columns = {}
        for relation, metadata in schema.items():
            namespace, name = relation.split(".")
            known_columns.setdefault(namespace, {})[name] = {
                column["name"]: "UNKNOWN" for column in metadata["columns"]
            }
        qualify(tree.copy(), dialect=dialect, schema=known_columns)
    except SqlglotError as exc:
        raise UnsafeQuery("Unable to resolve query sources") from exc
    # Bind every literal, including model-inlined user values, rather than trusting
    # the provider to separate SQL from data correctly.
    bound = {}
    used = set()
    for node in list(tree.walk()):
        if isinstance(node, exp.Placeholder):
            if node.name not in parameters:
                raise UnsafeQuery("A query parameter is missing")
            value = parameters[node.name]
            used.add(node.name)
        elif isinstance(node, exp.Literal):
            value = node.to_py()
        else:
            continue
        name = f"p{len(bound)}"
        bound[name] = value
        node.replace(exp.Placeholder(this=name))
    if used != set(parameters):
        raise UnsafeQuery("Unused query parameters are not permitted")
    try:
        normalized = tree.sql(dialect=dialect, comments=False,
                              unsupported_level=sqlglot.ErrorLevel.RAISE)
    except SqlglotError as exc:
        raise UnsafeQuery("SQL cannot be safely rendered for this dialect") from exc
    return ValidatedQuery(normalized, bound)
