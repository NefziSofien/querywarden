"""Command line. Exit codes: 0 pass, 1 gate failed, 2 configuration or setup error."""

from collections import Counter
from pathlib import Path

import psycopg
import typer

from querywarden.canary import find_canaries
from querywarden.compare import diff_rows, project_columns
from querywarden.config import Config, ConfigError, ProviderConfig, database_url, load_cases, load_config, load_policy
from querywarden.executor import ConnectionFailed, Executor, Status
from querywarden.gates import evaluate, load_baseline
from querywarden.lineage import load_catalog
from querywarden.preflight import PreflightFailed, run_preflight
from querywarden.providers.base import Provider
from querywarden.providers.command import CommandProvider
from querywarden.providers.http import HttpProvider, OpenAICompatibleProvider
from querywarden.providers.replay import RecordingError, RecordingProvider, RecordingStore, ReplayProvider
from querywarden.report import build_report, render_markdown, write_json
from querywarden.runner import Runner, validate_cases
from querywarden.verdict import PASSING, SAFETY_FAILURES, GoldQueryFailed

app = typer.Typer(add_completion=False, help="A CI gate for text-to-SQL features on PostgreSQL.")
ConfigOption = typer.Option(Path("querywarden/querywarden.yaml"), "--config", "-c", help="Path to querywarden.yaml")


def _fail_setup(message: object) -> typer.Exit:
    typer.secho(f"setup error: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(2)


def _build_live_provider(settings: ProviderConfig, base: Path) -> Provider:
    import os

    match settings.type:
        case "command":
            return CommandProvider(settings.argv)
        case "http":
            if not settings.url:
                raise ConfigError("the http provider needs a url")
            return HttpProvider(settings.url)
        case "openai":
            if not (settings.url and settings.model and settings.system_prompt_file):
                raise ConfigError("the openai provider needs url, model and system_prompt_file")
            prompt = (base / settings.system_prompt_file).read_text(encoding="utf-8")
            key = os.environ.get(settings.api_key_env) if settings.api_key_env else None
            return OpenAICompatibleProvider(settings.url, settings.model, prompt, api_key=key)
    raise ConfigError(f"provider type {settings.type} cannot make live calls")


def _build_provider(config: Config, base: Path, record: bool) -> Provider:
    settings = config.provider
    store = RecordingStore(base / settings.recordings) if settings.recordings else None
    if settings.type == "replay":
        if store is None:
            raise ConfigError("the replay provider needs a recordings file")
        return ReplayProvider(store, settings.id, settings.prompt_version)
    live = _build_live_provider(settings, base)
    if not record:
        return live
    if store is None:
        raise ConfigError("--record needs a recordings file in the provider settings")
    return RecordingProvider(live, store, settings.id, settings.prompt_version)


def _prepare(config_path: Path):
    config = load_config(config_path)
    base = config_path.parent
    url = database_url(config)
    policy = load_policy(base / config.policy)
    cases = load_cases(base / config.cases)
    run_preflight(url, list(policy.forbidden), config.allow_remote)
    with psycopg.connect(url, autocommit=True) as conn:
        catalog = load_catalog(conn)
    return config, base, policy, cases, Executor(url), catalog


@app.command()
def validate(config_path: Path = ConfigOption) -> None:
    """Check the database setup and that every gold query can be trusted."""
    try:
        _, _, policy, cases, executor, catalog = _prepare(config_path)
        issues = validate_cases(executor, catalog, policy, cases)
    except (ConfigError, PreflightFailed, ConnectionFailed, psycopg.OperationalError) as e:
        raise _fail_setup(e) from e
    for issue in issues:
        typer.echo(f"{issue.severity}: {issue.case_id}: {issue.message}")
    typer.echo(f"{len(cases)} cases checked, {len(issues)} issues")
    if any(issue.severity == "error" for issue in issues):
        raise typer.Exit(1)


@app.command()
def run(
    config_path: Path = ConfigOption,
    record: bool = typer.Option(False, help="Save live answers to the recordings file."),
    repeats: int = typer.Option(1, min=1, help="Ask each question this many times."),
    baseline: Path | None = typer.Option(None, help="A previous --json-out report to compare accuracy against."),
    allowed_drop: float = typer.Option(0.0, min=0.0, max=1.0, help="Accuracy drop to tolerate, from 0 to 1."),
    strict: bool = typer.Option(False, help="Also fail on newly failing cases, removed cases and unknown lineage."),
    json_out: Path | None = typer.Option(None, help="Write the JSON report here. It can be the next baseline."),
    markdown_out: Path | None = typer.Option(None, help="Write the Markdown report here."),
) -> None:
    """Run every case, apply the gates and report."""
    try:
        baseline_report = load_baseline(baseline) if baseline else None
        config, base, policy, cases, executor, catalog = _prepare(config_path)
        runner = Runner(executor, catalog, policy, _build_provider(config, base, record))
        results = [runner.run_case(case, attempt) for case in cases for attempt in range(repeats)]
    except (ConfigError, PreflightFailed, ConnectionFailed, GoldQueryFailed, RecordingError, OSError, psycopg.OperationalError) as e:
        raise _fail_setup(e) from e

    report = build_report(cases, results, config.provider.id, config.provider.prompt_version, repeats)
    gate = evaluate(report, baseline_report, allowed_drop, strict)
    if json_out:
        write_json(report, json_out)
    if markdown_out:
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(render_markdown(report, gate), encoding="utf-8")

    for result in results:
        if result.verdict in PASSING:
            continue
        safety = "SAFETY " if result.verdict in SAFETY_FAILURES else ""
        typer.echo(f"{safety}{result.verdict}: {result.case_id}: {result.detail}")
    counts = Counter(result.verdict.value for result in results)
    typer.echo(", ".join(f"{name}: {count}" for name, count in sorted(counts.items())))
    for warning in gate.warnings:
        typer.echo(f"warning: {warning}")
    if gate.comparison:
        typer.echo(
            f"baseline: {gate.comparison.baseline_accuracy:.1%} to {gate.comparison.current_accuracy:.1%}"
            f" on {gate.comparison.shared} shared cases"
        )
    for failure in gate.failures:
        typer.secho(f"gate failed: {failure}", fg=typer.colors.RED)
    typer.echo(f"accuracy: {report['summary']['passed']} of {report['summary']['evaluated']}")
    typer.echo("PASSED" if gate.passed else "FAILED")
    if not gate.passed:
        raise typer.Exit(1)


@app.command()
def review(
    case_id: str = typer.Argument(help="The id of the case to review."),
    config_path: Path = ConfigOption,
    max_rows: int = typer.Option(20, min=1, help="Print at most this many rows per side."),
) -> None:
    """Show one case: gold and model SQL, the verdict and a result diff."""
    try:
        config, base, policy, cases, executor, catalog = _prepare(config_path)
        case = next((c for c in cases if c.id == case_id), None)
        if case is None:
            raise ConfigError(f"no case with id {case_id}")
        runner = Runner(executor, catalog, policy, _build_provider(config, base, record=False))
        # run skips a disputed case, but review is where the dispute gets settled, so judge it anyway.
        result = runner.run_case(case.model_copy(update={"disputed": None}))
        gold = executor.execute(case.gold) if case.gold else None
    except (ConfigError, PreflightFailed, ConnectionFailed, GoldQueryFailed, RecordingError, OSError, psycopg.OperationalError) as e:
        raise _fail_setup(e) from e

    typer.echo(f"case: {case.id}")
    typer.echo(f"question: {case.question}")
    if case.tags:
        typer.echo(f"tags: {', '.join(case.tags)}")
    if case.disputed:
        typer.echo(f"disputed: {case.disputed} (run leaves this case out of accuracy)")
    typer.echo("\ngold SQL:" if case.gold else "\ngold: none, this case expects a refusal")
    if case.gold:
        typer.echo(_indent(case.gold))
    typer.echo("\nmodel SQL:" if result.sql else "\nmodel answer: no SQL")
    if result.sql:
        typer.echo(_indent(result.sql))
    safety = "SAFETY " if result.verdict in SAFETY_FAILURES else ""
    typer.echo(f"\nverdict: {safety}{result.verdict}" + (f": {result.detail}" if result.detail else ""))

    if result.verdict in SAFETY_FAILURES:
        typer.echo("rows withheld: the model query is a safety failure")
    elif gold is not None and result.sql:
        _print_diff(executor, policy.canaries, case, gold, result, max_rows)
    if result.verdict not in PASSING:
        raise typer.Exit(1)


def _print_diff(executor: Executor, canaries, case, gold, result, max_rows: int) -> None:
    """Rows are shown only when nothing suggests they hold data the policy protects."""
    if result.lineage_unknown:
        typer.echo("rows withheld: the columns the model query reads could not all be traced")
        return
    model = executor.execute(result.sql)
    if model.status is not Status.OK:
        return
    if find_canaries(model, canaries) or find_canaries(gold, canaries):
        typer.echo("rows withheld: the result contains a canary value")
        return

    typer.echo(
        f"\nshape: gold {len(gold.rows)} rows x {len(gold.columns)} columns,"
        f" model {len(model.rows)} rows x {len(model.columns)} columns"
    )
    if gold.truncated or model.truncated:
        typer.echo("a result was truncated, so the diff is incomplete")
    projected = project_columns(gold, model, case.options)
    if projected is None:
        typer.echo("the columns cannot be paired, so every row of each side is listed")
        only_gold, only_model, model_columns = gold.rows, model.rows, model.columns
    else:
        only_gold, only_model = diff_rows(gold.rows, projected, case.options)
        model_columns = gold.columns
    _print_rows("only in gold", gold.columns, only_gold, max_rows)
    _print_rows("only in model", model_columns, only_model, max_rows)


def _print_rows(title: str, columns, rows, max_rows: int) -> None:
    typer.echo(f"\n{title}: {len(rows)} rows")
    if not rows:
        return
    typer.echo(_indent(" | ".join(columns)))
    for row in rows[:max_rows]:
        typer.echo(_indent(" | ".join("NULL" if v is None else str(v) for v in row)))
    if len(rows) > max_rows:
        typer.echo(f"  ... {len(rows) - max_rows} more rows not shown")


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.strip().splitlines())


if __name__ == "__main__":
    app()
