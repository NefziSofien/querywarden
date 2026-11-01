"""Reports. Rows from the database are never included, so a report cannot leak query results."""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlgate.config import Case
from sqlgate.runner import CaseResult
from sqlgate.stats import wilson_interval
from sqlgate.verdict import PASSING, SAFETY_FAILURES, Verdict

if TYPE_CHECKING:
    from sqlgate.gates import GateResult

SCHEMA_VERSION = 1
_MAX_DETAIL_CHARS = 300


def build_report(
    cases: list[Case],
    results: Iterable[CaseResult],
    provider_id: str,
    prompt_version: str,
    repeats: int = 1,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    by_case: dict[str, list[CaseResult]] = {}
    for result in results:
        by_case.setdefault(result.case_id, []).append(result)
    entries = [_case_entry(case, by_case.get(case.id, [])) for case in cases]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(timespec="seconds"),
        "provider": {"id": provider_id, "prompt_version": prompt_version},
        "repeats": repeats,
        "cases": entries,
        "summary": _summary(entries),
    }


def _case_entry(case: Case, attempts: list[CaseResult]) -> dict[str, Any]:
    verdicts = [a.verdict for a in attempts]
    disputed = Verdict.DISPUTED in verdicts
    # With repeats, one bad attempt fails the case: a system that leaks sometimes still leaks.
    passed = bool(attempts) and not disputed and all(v in PASSING for v in verdicts)
    worst = next((a for a in attempts if a.verdict in SAFETY_FAILURES), None)
    worst = worst or next((a for a in attempts if a.verdict not in PASSING), None)
    shown = worst or (attempts[0] if attempts else None)
    return {
        "id": case.id,
        "tags": list(case.tags),
        "passed": passed,
        "disputed": disputed,
        "verdict": shown.verdict.value if shown else "not_run",
        "verdicts": [v.value for v in verdicts],
        "safety_failure": any(v in SAFETY_FAILURES for v in verdicts),
        "flaky": len(set(v in PASSING for v in verdicts)) > 1,
        "detail": (shown.detail if shown else "")[:_MAX_DETAIL_CHARS],
        "sql": shown.sql if shown else None,
        "lineage_unknown": sorted({note for a in attempts for note in a.lineage_unknown}),
        "latency_s": sum(a.latency_s for a in attempts),
        "input_tokens": _total(a.usage.input_tokens for a in attempts),
        "output_tokens": _total(a.usage.output_tokens for a in attempts),
        "cost_usd": _total(a.usage.cost_usd for a in attempts),
    }


def _total(values: Iterable[int | float | None]) -> int | float | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [e for e in entries if not e["disputed"]]
    passed = sum(e["passed"] for e in evaluated)
    low, high = wilson_interval(passed, len(evaluated))
    tags: dict[str, list[dict]] = {}
    for entry in evaluated:
        for tag in entry["tags"]:
            tags.setdefault(tag, []).append(entry)
    return {
        "evaluated": len(evaluated),
        "passed": passed,
        "accuracy": passed / len(evaluated) if evaluated else None,
        "accuracy_interval_95": [low, high],
        "disputed": len(entries) - len(evaluated),
        "safety_failures": sum(e["safety_failure"] for e in entries),
        "flaky": sum(e["flaky"] for e in entries),
        "by_tag": {
            tag: {"passed": sum(e["passed"] for e in group), "total": len(group)}
            for tag, group in sorted(tags.items())
        },
        "latency_s": sum(e["latency_s"] for e in entries),
        "input_tokens": _total(e["input_tokens"] for e in entries),
        "output_tokens": _total(e["output_tokens"] for e in entries),
        "cost_usd": _total(e["cost_usd"] for e in entries),
    }


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _cell(text: object) -> str:
    """Model output and error text are untrusted, and may end up in a PR comment."""
    escaped = html.escape(str(text), quote=False).replace("|", "\\|").replace("`", "'")
    return " ".join(escaped.split())


def render_markdown(report: dict[str, Any], gate: GateResult) -> str:
    summary = report["summary"]
    lines = [f"# sqlgate: {'PASSED' if gate.passed else 'FAILED'}", ""]
    if gate.failures:
        lines += ["## Why it failed", "", *[f"- {_cell(f)}" for f in gate.failures], ""]

    lines += ["## Accuracy", ""]
    if summary["accuracy"] is None:
        lines += ["No cases were evaluated.", ""]
    else:
        low, high = summary["accuracy_interval_95"]
        lines += [
            f"{summary['accuracy']:.1%} ({summary['passed']} of {summary['evaluated']} cases),"
            f" 95% interval {low:.1%} to {high:.1%}.",
            "",
        ]
    lines += _comparison_lines(gate)

    failed = [c for c in report["cases"] if not c["passed"] and not c["disputed"]]
    if failed:
        lines += ["## Failing cases", "", "| Case | Verdict | Safety | Detail |", "| --- | --- | --- | --- |"]
        for case in sorted(failed, key=lambda c: (not c["safety_failure"], c["id"])):
            safety = "yes" if case["safety_failure"] else ""
            lines.append(f"| {_cell(case['id'])} | {case['verdict']} | {safety} | {_cell(case['detail'])} |")
        lines.append("")

    if summary["by_tag"]:
        lines += ["## By tag", "", "| Tag | Passed |", "| --- | --- |"]
        lines += [f"| {_cell(t)} | {v['passed']} of {v['total']} |" for t, v in summary["by_tag"].items()]
        lines.append("")

    if gate.warnings:
        lines += ["## Warnings", "", *[f"- {_cell(w)}" for w in gate.warnings], ""]

    provider = report["provider"]
    lines += [
        "## Run",
        "",
        f"- Provider: {_cell(provider['id'])}, prompt version {_cell(provider['prompt_version'])}",
        f"- Date: {report['generated_at']}, repeats: {report['repeats']}",
        f"- Time waiting for answers: {summary['latency_s']:.1f}s",
        f"- Tokens: {_usage(summary['input_tokens'])} in, {_usage(summary['output_tokens'])} out,"
        f" cost: {'not reported' if summary['cost_usd'] is None else f'${summary['cost_usd']:.4f}'}",
    ]
    return "\n".join(lines) + "\n"


def _usage(value: int | float | None) -> str:
    return "not reported" if value is None else str(value)


def _comparison_lines(gate: GateResult) -> list[str]:
    comparison = gate.comparison
    if comparison is None:
        return []
    clarity = "statistically clear" if comparison.statistically_clear else "not statistically clear"
    lines = [
        f"Against the baseline, on {comparison.shared} shared cases: {comparison.baseline_accuracy:.1%} to"
        f" {comparison.current_accuracy:.1%}. The change is {clarity} (p = {comparison.p_value:.3f}).",
        "",
    ]
    for title, items in (
        ("Newly failing", comparison.regressed),
        ("Newly passing", comparison.improved),
        ("New cases", comparison.new),
    ):
        if items:
            lines += [f"- {title}: {', '.join(_cell(i) for i in items)}"]
    return [*lines, ""] if lines[-1] else lines
