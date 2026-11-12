# Redis (cache, sessions, rate limiters)

Use `redis_read` with a single read-only command.

- **Never `KEYS`.** Use `SCAN <cursor> MATCH <pattern> COUNT <n>` and follow the returned cursor until it is `0`.
- Inspect a key: `TYPE k`, `TTL k`, then the matching reader: `GET`, `HGETALL`, `LRANGE k 0 49`,
  `SMEMBERS`, `ZRANGE k 0 49 WITHSCORES`, `XRANGE`.
- `MEMORY USAGE k` shows a key's size; `DBSIZE` the key count; `INFO keyspace` the per-db summary.
- Session and token keys hold secrets: report existence, TTL and shape, not the raw values.
- Writes (`SET`, `DEL`, `EXPIRE`, `FLUSH*`, scripts, `CONFIG`) are refused.
