"""Run one case from question to verdict, and check that gold queries can be trusted."""

import time
from dataclasses import dataclass, field

from sqlgate.canary import find_canaries
from sqlgate.compare import Outcome, compare
from sqlgate.config import Case, Policy
from sqlgate.executor import ExecutionResult, Executor, Status
from sqlgate.lineage import Catalog, check_lineage
from sqlgate.providers.base import Provider, ProviderError, Usage
from sqlgate.verdict import GoldQueryFailed, Judgement, Verdict, judge_answer, judge_refused


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    verdict: Verdict
    detail: str = ""
    sql: str | None = None
    attempt: int = 0
    usage: Usage = Usage()
    latency_s: float = 0.0
    lineage_unknown: list[str] = field(default_factory=list)


class Runner:
    def __init__(self, executor: Executor, catalog: Catalog, policy: Policy, provider: Provider):
        self._executor = executor
        self._catalog = catalog
        self._policy = policy
        self._provider = provider
        self._gold_results: dict[str, ExecutionResult] = {}

    def run_case(self, case: Case, attempt: int = 0) -> CaseResult:
        if case.disputed:
            return CaseResult(case.id, Verdict.DISPUTED, case.disputed, attempt=attempt)

        started = time.monotonic()
        try:
            answer = self._provider.ask(case.question, attempt)
        except ProviderError as e:
            return CaseResult(case.id, Verdict.PROVIDER_ERROR, str(e), attempt=attempt)
        latency = time.monotonic() - started

        if answer.refused:
            judgement = judge_refused(case.expect == "refusal")
            return CaseResult(case.id, judgement.verdict, attempt=attempt, usage=answer.usage, latency_s=latency)

        judgement, unknown = self._judge_sql(case, answer.sql)
        return CaseResult(
            case.id, judgement.verdict, judgement.detail, answer.sql, attempt, answer.usage, latency, unknown
        )

    def _judge_sql(self, case: Case, sql: str) -> tuple[Judgement, list[str]]:
        lineage = check_lineage(sql, self._catalog, self._policy.forbidden)
        result = self._executor.execute(sql)
        violations = [*lineage.violations, *(f"canary {c}" for c in find_canaries(result, self._policy.canaries))]
        gold = self._gold(case)
        return judge_answer(result, gold, case.options, violations), lineage.unknown

    def _gold(self, case: Case) -> ExecutionResult | None:
        if case.gold is None:
            return None
        if case.id not in self._gold_results:
            self._gold_results[case.id] = self._executor.execute(case.gold)
        gold = self._gold_results[case.id]
        if gold.status is not Status.OK:
            raise GoldQueryFailed(f"{case.id}: {gold.detail}")
        return gold


@dataclass(frozen=True)
class Issue:
    case_id: str
    severity: str  # "error" stops the run, "warning" asks for a review
    message: str


def validate_cases(executor: Executor, catalog: Catalog, policy: Policy, cases: list[Case]) -> list[Issue]:
    issues = []
    ids_questions: dict[str, str] = {}
    for case in cases:
        if case.question in ids_questions:
            issues.append(Issue(case.id, "warning", f"same question as {ids_questions[case.question]}"))
        ids_questions.setdefault(case.question, case.id)
        if case.gold is not None:
            issues += _validate_gold(executor, catalog, policy, case)
    return issues


def _validate_gold(executor: Executor, catalog: Catalog, policy: Policy, case: Case) -> list[Issue]:
    first = executor.execute(case.gold)
    if first.status is not Status.OK:
        return [Issue(case.id, "error", f"gold query failed ({first.status}): {first.detail}")]

    issues = []
    lineage = check_lineage(case.gold, catalog, policy.forbidden)
    issues += [Issue(case.id, "error", f"gold query uses forbidden {v}") for v in lineage.violations]
    issues += [Issue(case.id, "warning", f"gold query: {u}") for u in lineage.unknown]
    issues += [Issue(case.id, "error", f"gold result contains canary {c}") for c in find_canaries(first, policy.canaries)]
    if first.truncated:
        issues.append(Issue(case.id, "error", "gold result was truncated"))
    if not first.rows:
        issues.append(Issue(case.id, "warning", "gold result is empty, so a query returning nothing would pass"))
    if case.ordered and "order by" not in " ".join(case.gold.lower().split()):
        issues.append(Issue(case.id, "warning", "ordered is set but the gold query has no ORDER BY"))
    if compare(first, executor.execute(case.gold), case.options).outcome is not Outcome.MATCH:
        issues.append(Issue(case.id, "error", "gold result changed between two runs"))
    return issues


__all__ = ["CaseResult", "GoldQueryFailed", "Issue", "Runner", "validate_cases"]
