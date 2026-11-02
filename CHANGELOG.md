# Changelog

## Unreleased

### Fixed

- The action uses `astral-sh/setup-uv@v7`, which runs on Node 24. Before, every run warned that Node 20 is deprecated.
- The report says "Tokens: not reported" once when a provider reports no usage, instead of "not reported in, not reported out".

### Security

- The remote-database check also looks at the server the connection actually reached. Before, a `hostaddr`, a `PGHOST` variable or a service file could point a URL that looked local at a remote server.
- A failed query is reported by its error class and SQLSTATE, never by the server's message. Some messages quote a value the query read, such as `invalid input syntax for type integer: "ada@example.test"`, so a model query could put a row value, canaries included, into the report and the PR comment.

## 0.2.0 (2026-11-02)

### Added

- `querywarden review <case>` shows one case: the question, the gold and model SQL, the verdict, and the rows only in each result, with `--max-rows` to cap them. It follows the same comparison rules as `run`. It never prints rows from a query that failed a safety check, a query whose columns cannot all be traced, or a result holding a canary value.
- Two real runs with `openai/gpt-oss-120b` on Groq, with their prompts, recorded answers and reports in `examples/bookshop/querywarden/`, and a write-up in [docs/real-model-run.md](docs/real-model-run.md).
- A README guide to setting querywarden up on your own schema.

### Fixed

- The HTTP and OpenAI-compatible providers wait as long as a `retry-after` header asks, up to 60 seconds. Before, a rate limit outlasted the short backoff and the case failed with no answer.
- A case marked disputed after the baseline was reported as removed, which failed `--strict`. It is now only reported as disputed.

### Changed

- The demo case `top-book-by-units` is disputed: four books tie at 3 units, so its gold query's answer was arbitrary.

## 0.1.0 (2026-11-01)

First release: result comparison and verdicts, the safe executor and preflight checks, lineage and canary privacy checks, the command, HTTP, OpenAI-compatible and replay providers, gates with baseline comparison and statistics, Markdown and JSON reports, the bookshop demo, and the GitHub Action.
