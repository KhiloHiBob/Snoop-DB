# PostgreSQL rules

(Distilled from the HiBob `db-connection` skill; generic, no private details.)

- **Filter by `company_id` first.** Multi-tenant tables carry a `company_id` column; every query on
  them needs `WHERE company_id = <value>` (or `IN (...)`). If the user did not give a company, ask
  or answer with schema/metadata only. Schema and metadata queries (`information_schema`,
  `pg_catalog`) are exempt.
- **Always bound the result.** The tool applies a LIMIT for you (500 by default, 50 for `SELECT *`).
  Prefer explicit column lists over `SELECT *`, and prefer aggregates (`count(*)`, `GROUP BY`) when
  the user asks "how many" or "which".
- **Wide columns** (text, jsonb, bytea): select only what is needed, or `length(col)` / `col->>'key'`.
  Never dump large payloads.
- **Read-only.** Only one SELECT (or plain EXPLAIN) per call. `EXPLAIN ANALYZE` executes the
  query and is refused. Writes, DDL and admin commands are refused in every environment.
- **Discover before querying.** `list_databases` -> `list_tables` -> `describe_table`. Do not guess
  table or column names; check them. Table names are usually `snake_case`; primary key `id`.
- **Replica awareness.** Production tunnels are often read replicas. For "is this recent data
  there?" questions, mention possible replication lag (use the health check).
- Dates are ISO (`2025-01-08`), times `12:52:35.963245`. Compare timestamps with ranges
  (`>= '2025-01-08' AND < '2025-01-09'`), not by casting the column.
- Soft-deleted, archived or inactive rows often exist; say whether you filtered them.
