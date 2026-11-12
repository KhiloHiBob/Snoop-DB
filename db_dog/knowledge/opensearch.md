# OpenSearch / Elasticsearch

Use `opensearch_search` (`method`, `path`, optional `body`). Only reads are allowed.

- Discover: `GET /_cat/indices`, `GET /<index>/_mapping`, `GET /_cluster/health`.
- Search: `POST /<index>/_search` with a JSON query body; `size` is capped at 200. `GET /<index>/_count` for counts.
- Prefer filters (`term`, `range`) over scoring queries when the user wants exact matches.
- Scripts (`script`, `script_fields`, `script_score`) are refused.
- Index names can use wildcards (`logs-*`) but be specific: broad patterns are slow.
