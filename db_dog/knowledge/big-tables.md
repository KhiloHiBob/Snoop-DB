# Big tables and tenant data: always ask for the company

Large tables (millions of rows: events, audit logs, history, assignments) and any table with a
`company_id` column must never be scanned blind.

1. `describe_table` tells you `has_company_id`, `approx_rows` and `is_big_table`. Check it BEFORE querying
   any table you have not described yet.
2. If the table is big or has `company_id`, and the user did not give a company id: **stop and ask
   "Which company id should I use?"** Do not guess, do not try company 1, do not loop over companies,
   do not run the query "just to see". Your answer in that turn is the question, with the exact query you
   will run once you have the id.
3. Once you have a company id, always put `company_id = <id>` in the WHERE clause, plus the narrowest
   filters the user gave (employee, request, date range), explicit columns (no `SELECT *`) and `ORDER BY`
   the time column descending with a small LIMIT.
4. Big tables are enforced in code in every environment (local too): a query without the company filter is
   rejected with a message telling you to ask the user.
5. Ids like "employee 1" may be a string or a number depending on the column type; check the column type
   from `describe_table` and quote accordingly.
6. Counting a big table without a company filter is also a full scan: do not `count(*)` it. If the user
   wants a size, use `approx_rows` from `describe_table` and say it is an estimate.
7. Catalog queries (`information_schema`, `pg_catalog`) are cheap and exempt, use them freely to find tables.
8. EXCEPTION, platform-wide questions ("how many employees are there in Bob", "total number of companies"):
   the user wants a number across ALL companies, so do NOT ask for a company id. Run a pure aggregate on one
   table with no company filter, e.g. `SELECT count(*) AS employees FROM employee`. The guard allows this
   (single table, aggregates only, optional GROUP BY on a low-cardinality column such as `status`, never by
   company/employee/id). Say the number is platform-wide, and mention what it includes (e.g. all lifecycle
   statuses) or add a `GROUP BY status` breakdown. Never return per-company or per-employee rows this way.
   Never answer from `approx_rows` when a real count is possible.
