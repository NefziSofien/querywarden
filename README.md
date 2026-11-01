# sqlgate

A CI gate for text-to-SQL features on PostgreSQL. Work in progress: only the safe executor exists so far.

## Run the tests

Docker is required, because the safety tests run against a real PostgreSQL server.

```bash
uv run pytest
SQLGATE_PG_IMAGE=postgres:16-alpine uv run pytest
```

## Known limits

- The row and byte caps are checked after each batch reaches the client, so one enormous value can use a lot of client memory before the query is cut off.
- Single-label hostnames are treated as local, so Docker service names work in CI. The role checks still apply.
- Do not point this tool at a production database.
