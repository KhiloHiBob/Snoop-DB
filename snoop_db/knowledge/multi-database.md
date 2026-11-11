# Many databases behind one server

(From the HiBob `multi-database-queries` skill.)

A single server often hosts one database per service. To answer "which database has table X" or
"run this everywhere":

- **Never hardcode database names.** Call `list_databases`; system databases are already excluded.
- To check where a table or column exists, use `fan_out_query` with a metadata query such as
  `SELECT table_name FROM information_schema.tables WHERE table_name = 'x'`. The result gains a
  `_database` column.
- Test the query on one database first (`run_sql`), then fan out. A fan-out is capped in the number
  of databases; narrow it with `database_like` when you can.
- Databases that error out (no permission, missing table) are skipped and listed in the notes, they
  do not fail the whole answer.
- Cross-company aggregates on tenant tables need the same `company_id` care as single queries.
