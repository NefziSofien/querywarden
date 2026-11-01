# querywarden

A CI gate for text-to-SQL features on PostgreSQL. A prompt or model change can return wrong numbers, expose forbidden data or attempt a dangerous statement without any visible error. querywarden asks your questions, runs the generated SQL on a test database with a limited role, compares the results with trusted queries, and fails the build.

- **It compares results, not SQL text**, so a different query with the same answer still passes.
- **Safety and accuracy stay separate**, so a high average cannot hide a data leak.

See it block a real pull request in [querywarden-example](https://github.com/NefziSofien/querywarden-example/pull/1). That example and the demo below use hand-written answers. No results from a real model have been published yet.

Status: early, version 0.1.0.

## Install

```bash
pip install querywarden-cli
```

The package is named `querywarden-cli`, because PyPI refuses a name this close to the unrelated `query-warden` project. The command and the import are both `querywarden`. Python 3.12 or later is required.

## Use it in GitHub Actions

```yaml
- run: docker compose up -d --wait # your test database
- uses: NefziSofien/querywarden@v0.1.0
  with:
    baseline: querywarden/baseline.json
    comment-on-pr: "true"
```

The job fails when a gate fails, and the report goes to the job summary. See [docs/github-action.md](docs/github-action.md) for the inputs, outputs and a full workflow. Reports contain the generated SQL and error messages, so think before turning on PR comments in a public repository.

## Try the demo

From a clone of this repository, the demo needs Docker and uv, and no API key. It replays hand-written answers against a small invented bookshop, with failures planted on purpose. Those answers are not the output of any model, so the result says nothing about model quality.

```bash
docker compose -f examples/bookshop/compose.yaml up -d --wait
export QUERYWARDEN_DATABASE_URL=postgresql://querywarden_reader:demo-only@127.0.0.1:55432/bookshop
uv run querywarden validate --config examples/bookshop/querywarden/querywarden.yaml
uv run querywarden run --config examples/bookshop/querywarden/querywarden.yaml
docker compose -f examples/bookshop/compose.yaml down
```

`querywarden run` exits with 1 because of the planted safety failures. Exit codes: 0 pass, 1 gate failed, 2 configuration or setup error.

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
QUERYWARDEN_PG_IMAGE=postgres:16-alpine uv run pytest
```

## Known limits

- The row and byte caps are checked after each batch reaches the client, so one enormous value can use a lot of client memory before the query is cut off.
- Single-label hostnames are treated as local, so Docker service names work in CI. The role checks still apply.
- The privacy checks catch direct uses of forbidden columns, including through views, and planted values in results. They do not judge what could be inferred from aggregates, and SQL that cannot be analyzed is reported as unknown, not as safe.
- Do not point this tool at a production database.

## License

MIT
