# sqlgate

A CI gate for text-to-SQL features on PostgreSQL. Work in progress: gates, baselines and reports are not built yet.

## Try the demo

The demo needs Docker and uv, and no API key. It replays hand-written answers against a small invented bookshop, with failures planted on purpose. Those answers are not the output of any model, so the result says nothing about model quality.

```bash
docker compose -f examples/bookshop/compose.yaml up -d --wait
export SQLGATE_DATABASE_URL=postgresql://sqlgate_reader:demo-only@127.0.0.1:55432/bookshop
uv run sqlgate validate --config examples/bookshop/sqlgate/sqlgate.yaml
uv run sqlgate run --config examples/bookshop/sqlgate/sqlgate.yaml
docker compose -f examples/bookshop/compose.yaml down
```

`sqlgate run` exits with 1 because of the planted failures. Exit codes: 0 pass, 1 cases failed, 2 configuration or setup error.

## Run the tests

Docker is required, because the safety tests run against a real PostgreSQL server.

```bash
uv run pytest
SQLGATE_PG_IMAGE=postgres:16-alpine uv run pytest
```

## Known limits

- The row and byte caps are checked after each batch reaches the client, so one enormous value can use a lot of client memory before the query is cut off.
- Single-label hostnames are treated as local, so Docker service names work in CI. The role checks still apply.
- The privacy checks catch direct uses of forbidden columns, including through views, and planted values in results. They do not judge what could be inferred from aggregates, and SQL that cannot be analyzed is reported as unknown, not as safe.
- Do not point this tool at a production database.
