---
name: snoop-db
description: Answer questions about data in any configured database environment (local, staging, sandbox, prod) through the local Snoop-DB read-only agent. Use when the user asks to look up, count, list or describe database tables/rows/keys, or asks which database holds some data.
---

# Snoop-DB

A local, read-only database agent. Start it with `python -m snoop_db` (listens on `127.0.0.1:8000`).

## Ask a question (the agent writes the SQL)

```bash
curl -s localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"message": "how many active employees per department?", "environment": "auto"}'
```

`environment` is `auto` (picked from the wording; default local) or an explicit name from
`GET /api/environments`. The response always contains `environment`, so **state which
environment was queried** when you answer.

## Run one statement yourself (no LLM)

```bash
curl -s localhost:8000/api/query -H 'content-type: application/json' \
  -d '{"environment": "local", "store": "postgres", "database": "snoop_demo", "statement": "SELECT count(*) FROM employees WHERE company_id = 1"}'
```

## Rules

- Everything is read-only in every environment; writes are rejected (HTTP 422).
- Tenant tables need a `company_id` filter; it is enforced on production environments.
- If the API reports a missing tunnel, tell the user which command to run (`sdm connect ...`); never guess ports or credentials.
- Never paste customer data from production results beyond what the user needs.
