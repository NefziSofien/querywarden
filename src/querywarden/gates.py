"""Decide pass or fail. Safety and accuracy are judged apart, so an average can never hide a leak."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from querywarden.config import ConfigError
from querywarden.report import SCHEMA_VERSION
from querywarden.stats import mcnemar_exact

_CLEAR_P_VALUE = 0.05


@dataclass(frozen=True)
class Comparison:
    shared: int
    baseline_accuracy: float
    current_accuracy: float
    regressed: list[str]
    improved: list[str]
    new: list[str]
    removed: list[str]
    p_value: float

    @property
    def statistically_clear(self) -> bool:
        return self.p_value < _CLEAR_P_VALUE


@dataclass(frozen=True)
class GateResult:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    comparison: Comparison | None = None

    @property
    def passed(self) -> bool:
        return not self.failures


def load_baseline(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read baseline {path}: {e.strerror}") from e
    except ValueError as e:
        raise ConfigError(f"baseline {path} is not valid JSON") from e
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError(f"baseline {path} needs schema_version {SCHEMA_VERSION}; regenerate it with --json-out")
    cases = data.get("cases")
    if not isinstance(cases, list) or not all(
        isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("passed"), bool) for c in cases
    ):
        raise ConfigError(f"baseline {path} has a malformed cases list")
    return data


def evaluate(
    report: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    allowed_drop: float = 0.0,
    strict: bool = False,
) -> GateResult:
    failures: list[str] = []
    warnings: list[str] = []
    cases = report["cases"]

    for case in cases:
        if case["safety_failure"]:
            failures.append(f"safety: {case['id']} ({case['verdict']})")
        if "provider_error" in case["verdicts"]:
            # An unanswered case proves nothing, so an outage must not look like a pass.
            failures.append(f"no answer: {case['id']}")
        if case["flaky"]:
            warnings.append(f"flaky: {case['id']} gave different results across repeats")
        for note in case["lineage_unknown"]:
            message = f"unknown lineage: {case['id']}: {note}"
            (failures if strict else warnings).append(message)

    evaluated = [c for c in cases if not c["disputed"]]
    if not evaluated:
        failures.append("no cases were evaluated")
    disputed = [c["id"] for c in cases if c["disputed"]]
    if disputed:
        warnings.append(f"excluded as disputed: {', '.join(disputed)}")

    comparison = None
    if baseline is None:
        warnings.append("no baseline given, so accuracy was reported but not gated")
    else:
        comparison = _compare(cases, evaluated, baseline)
        failures += _accuracy_failures(comparison, allowed_drop, strict)
        if comparison.removed:
            message = f"cases in the baseline but not in this run: {', '.join(comparison.removed)}"
            (failures if strict else warnings).append(message)
    return GateResult(failures, warnings, comparison)


def _compare(cases: list[dict], evaluated: list[dict], baseline: dict[str, Any]) -> Comparison:
    before = {c["id"]: c for c in baseline["cases"] if not c.get("disputed")}
    now = {c["id"]: c for c in evaluated}
    # A case disputed since the baseline is still in the run, and is already reported as disputed.
    in_run = {c["id"] for c in cases}
    shared = sorted(before.keys() & now.keys())
    regressed = [i for i in shared if before[i]["passed"] and not now[i]["passed"]]
    improved = [i for i in shared if now[i]["passed"] and not before[i]["passed"]]
    # Only shared cases count, so removing a failing case cannot raise the score.
    count = len(shared) or 1
    return Comparison(
        len(shared),
        sum(before[i]["passed"] for i in shared) / count,
        sum(now[i]["passed"] for i in shared) / count,
        regressed,
        improved,
        sorted(now.keys() - before.keys()),
        sorted(before.keys() - in_run),
        mcnemar_exact(len(regressed), len(improved)),
    )


def _accuracy_failures(comparison: Comparison, allowed_drop: float, strict: bool) -> list[str]:
    failures = []
    if comparison.shared == 0:
        failures.append("the baseline shares no cases with this run")
    drop = comparison.baseline_accuracy - comparison.current_accuracy
    if drop > allowed_drop + 1e-12:
        failures.append(
            f"accuracy fell from {comparison.baseline_accuracy:.1%} to {comparison.current_accuracy:.1%}"
            f" on {comparison.shared} shared cases (allowed drop {allowed_drop:.1%})"
        )
    if strict:
        failures += [f"newly failing: {case_id}" for case_id in comparison.regressed]
    return failures
