"""Command line. Exit codes: 0 pass, 1 cases failed, 2 configuration or setup error."""

from collections import Counter
from pathlib import Path

import psycopg
import typer

from sqlgate.config import Config, ConfigError, ProviderConfig, database_url, load_cases, load_config, load_policy
from sqlgate.executor import ConnectionFailed, Executor
from sqlgate.lineage import load_catalog
from sqlgate.preflight import PreflightFailed, run_preflight
from sqlgate.providers.base import Provider
from sqlgate.providers.command import CommandProvider
from sqlgate.providers.http import HttpProvider, OpenAICompatibleProvider
from sqlgate.providers.replay import RecordingError, RecordingProvider, RecordingStore, ReplayProvider
from sqlgate.runner import Runner, validate_cases
from sqlgate.verdict import PASSING, SAFETY_FAILURES, GoldQueryFailed

app = typer.Typer(add_completion=False, help="A CI gate for text-to-SQL features on PostgreSQL.")
ConfigOption = typer.Option(Path("sqlgate/sqlgate.yaml"), "--config", "-c", help="Path to sqlgate.yaml")


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
) -> None:
    """Run every case and print one verdict per case."""
    try:
        config, base, policy, cases, executor, catalog = _prepare(config_path)
        runner = Runner(executor, catalog, policy, _build_provider(config, base, record))
        results = [runner.run_case(case, attempt) for case in cases for attempt in range(repeats)]
    except (ConfigError, PreflightFailed, ConnectionFailed, GoldQueryFailed, RecordingError, OSError, psycopg.OperationalError) as e:
        raise _fail_setup(e) from e

    for result in results:
        if result.verdict in PASSING:
            continue
        safety = "SAFETY " if result.verdict in SAFETY_FAILURES else ""
        typer.echo(f"{safety}{result.verdict}: {result.case_id}: {result.detail}")
    for note in sorted({(r.case_id, u) for r in results for u in r.lineage_unknown}):
        typer.echo(f"unknown lineage: {note[0]}: {note[1]}")
    counts = Counter(result.verdict.value for result in results)
    typer.echo(", ".join(f"{name}: {count}" for name, count in sorted(counts.items())))
    if any(result.verdict not in PASSING and result.verdict.value != "disputed" for result in results):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
