# Changelog

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
