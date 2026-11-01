# sqlgate

A CI gate for text-to-SQL features on PostgreSQL. Work in progress: the GitHub Action and a package release are not done yet.

## Try the demo

The demo needs Docker and uv, and no API key. It replays hand-written answers against a small invented bookshop, with failures planted on purpose. Those answers are not the output of any model, so the result says nothing about model quality.

```bash
docker compose -f examples/bookshop/compose.yaml up -d --wait
export SQLGATE_DATABASE_URL=postgresql://sqlgate_reader:demo-only@127.0.0.1:55432/bookshop
uv run sqlgate validate --config examples/bookshop/sqlgate/sqlgate.yaml
uv run sqlgate run --config examples/bookshop/sqlgate/sqlgate.yaml
docker compose -f examples/bookshop/compose.yaml down
```

`sqlgate run` exits with 1 because of the planted safety failures. Exit codes: 0 pass, 1 gate failed, 2 configuration or setup error.

## Gates

- **Safety:** any policy violation, blocked statement or answered refusal case fails the run. This has no tolerance setting.
- **No answer:** any provider error fails the run, so an outage cannot look like a pass.
- **Accuracy:** only gated when you pass `--baseline`, using a report saved earlier with `--json-out`. Add `--allowed-drop 0.02` to tolerate a small drop. Only cases present in both runs count, so removing a failing case cannot raise the score.
- **`--strict`:** also fails on any newly failing case, any case removed since the baseline, and any query whose lineage could not be analyzed.

Reports show a 95% interval, and whether a change against the baseline is statistically clear. They never contain rows returned by the database, but they do contain the generated SQL and error messages, so treat them as internal.

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
