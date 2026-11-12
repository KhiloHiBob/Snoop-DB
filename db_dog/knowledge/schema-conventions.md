# Schema conventions (use them to guess where things live, then verify)

(From HiBob's `db-schema` rules.)

- Tables are `snake_case`; primary key is `id BIGSERIAL`; tenant tables have `company_id BIGINT NOT NULL`.
- Text columns are `TEXT` (not VARCHAR); semi-structured data is `JSONB`.
- Indexes on tenant tables lead with `company_id`, so queries filtered by company are fast.
- Timestamps are usually `created_at` / `updated_at`; many tables are append-only audit logs.
- Flyway migrations (`V<timestamp>__name.sql`) are the schema history; the live catalog is the
  authority for what exists right now. Always confirm with `describe_table`.
- No `ON DELETE CASCADE`: deletes are explicit, so orphan rows are possible. Mention it if relevant.
