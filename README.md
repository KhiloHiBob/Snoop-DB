# db-dog

An AI database agent that turns plain English into SQL, explores schemas, and digs up answers—because life’s too short to argue with JOINs.

It is **read-only**. Ask ("how many employees per department?", "which database has the
`time_off_requests` table?") and get the answer plus a sortable table, with the exact SQL shown and the
environment stated.

- Postgres (including fan-out across many databases on one server), SingleStore/MySQL, Redis, MongoDB, OpenSearch
- Any number of environments (local, staging, sandbox, prod, ...); **read-only in every environment**
- Safety enforced in code, not just in the prompt (see below)
- A chat page (`db_dog/web/index.html`) with environment selector, tables, CSV export, light/dark theme

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
docker compose up -d                      # demo Postgres on 127.0.0.1:55432
cp config/config.example.yaml config/local.yaml   # then edit (git-ignored)
# The chat uses your own local Claude login (`claude`, Claude Code CLI): no API key needed.
# To use the Anthropic API instead: set llm.backend: anthropic_api and export ANTHROPIC_API_KEY.
.venv/bin/python -m db_dog                # http://127.0.0.1:8137
```

The page has **no login**: the server only binds to loopback and checks the Host/Origin headers.
Do not expose the port.

## Configuration

`config/local.yaml` (git-ignored) holds your environments: host/port, user, database, and which stores exist.
Reference secrets with `password_env: NAME` instead of writing them down. The committed
`config/config.example.yaml` has placeholders only. Environments marked `production: true` also
enforce a `company_id` predicate and hide result rows from the LLM. You can add private prompt notes as
`knowledge_local/*.md` (git-ignored). The environment is picked from the question wording ("... in prod"),
default `local`, or from the selector.

## Safety model

1. **SQL guard** (`db_dog/safety/sql_guard.py`, sqlglot): exactly one statement; SELECT / plain EXPLAIN only;
   rejects DML, DDL, `SELECT INTO`, `FOR UPDATE`, `EXPLAIN ANALYZE`, data-modifying CTEs, and dangerous
   functions (`pg_sleep`, `pg_read_file`, `lo_*`, `dblink`, `set_config`, ...). The re-serialised AST is what runs.
2. **Database-level read-only**: read-only transaction, `default_transaction_read_only`, statement timeout, always rolled back.
3. **Policy**: LIMIT injected (500; 50 for `SELECT *`), result byte cap, `company_id` predicate required on
   tenant tables where enforced, and a "limit reached, more rows may exist" flag.
4. **Other stores** use operation allowlists (Redis read commands, Mongo find/aggregate stages, OpenSearch GET/_search).
5. **Privacy**: the LLM sees schemas and your question, **not result rows** (the browser gets rows directly).
   A UI toggle can let it read rows in non-prod; prod needs `llm.allow_prod_rows_to_llm: true`.
6. **Missing tunnel** gives a clear hint (`sdm connect <resource>`); ports and credentials are never guessed.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | `{message, environment: "auto" or a name, llm_sees_rows, history}` -> summary, tables, steps |
| `POST /api/query` | one read-only statement, no LLM (`{environment, store, database, statement}`) |
| `GET /api/environments` | names, production flag, stores (never hosts/ports) |
| `GET /api/environments/{name}/health` | role, replica status, write-grant warning |

## Adding a store or dialect

Implement a connector in `db_dog/connectors/` (an allowlist validator plus `query`/`run`), register it in
`connectors/__init__.py`, add tool schemas in `agent/tools.py` and a recipe in `db_dog/knowledge/`.

## Tests

`.venv/bin/python -m pytest` (no database or API key needed).
