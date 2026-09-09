# Shanghai chemistry retrieval gateway v1

This package exposes only `RetrievalGateway`, `RetrievalGatewayError`, and
`query_retrieval(payload)`.

It reads the pinned live `index.sqlite3`, `config.json`, and
`retrieval_core.py` as one source graph, deserializes the captured database
bytes into SQLite `:memory:`, reuses the pinned `query_records` and
`purpose_preflight`, and revalidates the complete graph after the query.
SQLite never receives a live database path.

Accepted payload keys are limited to `query`, `purpose`, `limit`, `K`, `A`,
`C`, `R`, `D`, `strict_tags`, `source_family`, `temporal_role`,
`official_only`, `authority_scope`, and `claim_year`. Paths, URIs, private or
student scopes, `include_excluded`, and unknown fields fail closed.

Responses contain teacher-safe metadata and previews only. They always state
`read_only=true`, `content_exposed=false`, `source_file_exposed=false`, and
`human_reviewed=false`; source paths and full content are never returned.
