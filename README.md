# Snoop-DB
An AI database agent that turns plain English into SQL, explores schemas, and digs up answers—because life’s too short to argue with JOINs.

## Run the local demo

Requires Docker Compose. From the repository root:

```sh
cp .env.example .env
# Edit .env: replace the admin and reader passwords with different local passwords.
# Set PGPASSWORD to the same reader password for running Python outside Docker.
docker compose up --build
```

Open **http://localhost:8000**. No API key is needed for `LLM_PROVIDER=demo`.
Try:

- **Show schema** — tables, the `order_summary` view, columns, foreign keys,
  constraints, and indexes.
- **Show customers** — SQL plus three customer rows.
- **Orders for Ada** — a bound name parameter.
- **Order count per customer** — a left join, including customers with no orders.
- **Total paid order amount** — returns 370.00.
- **Show plan for customers** — estimated PostgreSQL plan, without `ANALYZE`.
- **Explain indexes** — suggestions only.
- **What is our revenue?** — asks for a business definition instead of guessing.

The offline provider is deliberately limited to these sample-schema templates.
Use an LLM provider for general language understanding and follow-up questions.
Conversation context consists of the last six user questions, held in browser memory;
no chat or database rows are persisted by the application.

The database is initialized only on the first startup. Changing passwords or sample
SQL does not change an existing volume. To reset **and delete all demo data**:
`docker compose down -v`, then start again. The database and app ports bind to
loopback only. This MVP has no authentication; **do not expose it publicly**.
Add authentication, authorization, TLS, request-size/rate limits, and deployment-specific
observability before a shared deployment. Anyone with access to the app can read its
configured allowlist.

### Run Python locally

Requires Python 3.12+ and a running, provisioned PostgreSQL database:

```sh
python -m venv .venv
. .venv/bin/activate
pip install .
set -a
. ./.env
set +a
snoop-db
```

Alternatively, `uv sync --frozen` installs the committed lockfile.
Environment files are not loaded automatically by Python; the shell commands above
load them. Keep shell quotes around values containing spaces or shell metacharacters.
Compose sets libpq's `PGPASSWORD` from `SNOOP_DB_PASSWORD`; the Python process uses
`PGPASSWORD` directly. No URL password escaping is needed.

### Configure an LLM

Set `LLM_PROVIDER=openai`, `LLM_API_KEY`, `LLM_MODEL`, and optionally `LLM_BASE_URL`
(an HTTPS OpenAI-compatible `/v1` endpoint supporting JSON chat completions).
Restart the app after configuration changes. The model receives the user question,
previous user questions, dialect, and allowlisted schema metadata, **not credentials
or query results**. Schema comments and row samples are never included in discovery.
Review schema identifiers, constraints and index definitions before sending metadata
to an external provider: they can themselves contain sensitive information.

`SEND_RESULTS_TO_LLM=false` is the default. Results are rendered locally with a
row-count summary and the provider's pre-execution SQL explanation. Set it to `true`
only if you explicitly approve sending the returned rows (or estimated plan) to the
configured provider for a second, natural-language summary. This includes up to
`MAX_ROWS` rows, not just aggregates. The UI displays this privacy setting.

Other environment settings:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | libpq connection string or PostgreSQL URL; SELECT-only login |
| `ALLOWED_RELATIONS` | Required comma-separated **explicit** `schema.table`/`schema.view` names; no wildcards |
| `DB_DIALECT` | `postgres`; other dialects fail closed until implemented |
| `MAX_ROWS` | Returned-row cap, 1–1000; default 100 |
| `STATEMENT_TIMEOUT_MS` | Query and lock timeout, 1–60000; default 5000 |

The initial allowlist supports lowercase ASCII identifiers. Database errors are
sanitized instead of returning connection strings, SQL error details, or provider
responses. Treat displayed bound parameter values as potentially sensitive; the
application does not log query values.

## Read-only security model and permissions

The model is **not** a security boundary. User prompts, database cells, and schema
metadata cannot grant permission or bypass these independent controls:

1. **Least-privilege login.** `demo/sample.sql` creates `snoop_reader` with `SELECT`
   only on three explicit relations. It has no role memberships, elevated role
   attributes, database `CREATE`/`TEMP`, schema `CREATE`, sequence writes, or
   execution rights on user-defined routines. The app checks these properties on
   every connection and rejects misconfigured accounts (including the admin).
   It also rejects SELECT privileges on other user relations and any table/column
   write privileges. PostgreSQL's normal system-catalog visibility remains necessary
   for discovery, but generated catalog queries are blocked.
2. **Read-only transactions.** Every discovery/query connection begins a
   read-only transaction, sets a `pg_catalog`-only search path and local timeouts,
   and always rolls back. The role also defaults to read-only. This complements,
   rather than replaces, restricted grants.
3. **Parser policy.** SQLGlot parses the configured dialect. A fail-closed AST
   grammar allows one SELECT (safe non-recursive CTEs and set operations included).
   Base relations must be schema-qualified and allowlisted, including inside
   subqueries and CTEs. Writes, DDL, administration, multiple statements,
   `SELECT INTO`, locks, table functions, unknown syntax, qualified functions,
   unsafe/unapproved functions and non-primitive casts are rejected.
   The app executes **regenerated validated SQL**, never the original model string.
4. **Bound data and bounded execution.** Named parameters and even model-inlined
   literals become driver-bound values. An outer limit plus bounded fetching caps
   results; timeouts bound expensive queries. Plans use only server-generated
   `EXPLAIN (FORMAT JSON)`, never `ANALYZE`.
5. **No privileged tools.** The provider has no database connection or command
   execution tools. Advisory text cannot execute changes. The browser uses
   `textContent`, not HTML interpolation, for model and database output.

For an existing database, have its administrator provision a fresh dedicated role
using the grants/revokes in `demo/sample.sql` as a template. Adapt the database and
explicit relation names; do not run the sample inserts on a real database.
Revoking PUBLIC privileges affects other users, so review those changes with the DBA.
Do not grant all/future tables or membership in broad reader roles. Revoke PUBLIC
execution rights on custom functions/procedures and set appropriate default
privileges for **every object-creating owner**.

Only allowlist DBA-reviewed tables and views. Views, row-security policies,
custom types/operators, and database extensions are trusted database code:
SELECT-only permissions and AST validation cannot prove their implementations
are side-effect-free or prevent external effects hidden inside them.
Use built-in data types and vetted views/policies; do not give this role access
to unsafe routines or untrusted database code. Row limits do not cap individual
cell sizes or all server resources; use PostgreSQL resource controls as needed.
The conservative function/syntax allowlist intentionally rejects some otherwise
read-only PostgreSQL features. Parser validation is defense in depth, not a
replacement for database permissions.

## Architecture

```text
Browser → FastAPI → Agent → allowlisted schema discovery → provider generation
                        → AST validation → read-only execution → local SQL/results
                                                            ↘ optional LLM summary
```

Modules under `src/snoop_db/`:

- `config.py`: environment configuration and limits.
- `schema.py`: metadata-only PostgreSQL introspection.
- `generation.py`: provider interface by convention, offline templates and
  OpenAI-compatible JSON generation; clarification and advice responses.
- `validation.py`: dialect-aware grammar, relation policy and parameter binding.
- `execution.py`: revalidation at the execution boundary, result limits and plans.
- `dialects.py`: adapter protocol, registry, privilege checks and read-only sessions.
- `agent.py`: orchestration; only explicit configuration enables result sharing.
- `api.py`, `static/`: synchronous FastAPI handlers (threadpool), simple chat UI.

Endpoints: `GET /`, `GET /health` (configuration status, not DB readiness),
`GET /api/schema`, and `POST /api/chat` with
`{"question":"orders for Ada","history":[]}`.
Query responses contain schema metadata and `result.sql`, `parameters`, `columns`,
`rows`, `truncated`, and `explain`. Clarification/advice never executes generated SQL.

### Adding MySQL, SQL Server, or SQLite

Do not just change the parser dialect. Implement and register a complete `Dialect`
adapter in `DIALECTS` with:

1. Its SQLGlot dialect name (`mysql`, `tsql`, `sqlite`).
2. Metadata discovery returning the same schema structure, filtered by explicit
   permissions/allowlist.
3. A connection context enforcing least privilege, read-only transactions or the
   engine's equivalent, safe name resolution, query/lock timeouts and rollback.
4. Driver-native binding for the validator-generated `:p0` placeholders.
5. Dialect-specific execution limits and safe estimated-plan support in the
   executor (currently its wrapping and EXPLAIN syntax target PostgreSQL).
6. Adversarial validator and live-database tests proving all security invariants.

SQLite needs file-level read-only opening and an authorizer; SQL Server and MySQL
need their own privilege and transaction enforcement. Keep unsupported features
fail-closed rather than claiming safety based on parsing alone.

## Tests

Uses Python's standard-library `unittest`; no separate test dependencies:

```sh
uv run python -m unittest discover -s tests -v
```

Live integration tests are opt-in against the initialized disposable demo:

```sh
docker compose up -d db
# Load your local environment (including DATABASE_URL and PGPASSWORD) first.
export TEST_DATABASE_URL="$DATABASE_URL"
uv run python -m unittest discover -s tests -v
```

Tests cover unsafe SQL, CTE/subquery allowlists, parameterization, privacy, API
behavior, and (when enabled) schema discovery, read-only transactions, permissions,
timeouts, result truncation and the sample chat/plan flow.
