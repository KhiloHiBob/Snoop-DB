# SingleStore (MySQL protocol)

Used for high-volume, per-tenant "objects" style data. Same safety rules as Postgres.

- Use `list_databases(store="singlestore")`, `list_tables`, `describe_table` first.
- MySQL dialect: backtick-quote identifiers, `LIMIT n`, no `ILIKE` (use `LIKE`, case-insensitive by collation).
- `SHOW TABLES`, `SHOW COLUMNS FROM t` and `DESCRIBE t` are allowed; everything else must be one SELECT.
- Columnstore tables are fast for aggregates and slow for point lookups by non-key columns.
- Table row counts in `information_schema.tables.table_rows` are approximate.
