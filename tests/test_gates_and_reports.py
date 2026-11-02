import json
from datetime import UTC, datetime

import pytest

from querywarden.config import Case, ConfigError
from querywarden.gates import evaluate, load_baseline
from querywarden.providers.base import Usage
from querywarden.report import build_report, render_markdown, write_json
from querywarden.runner import CaseResult
from querywarden.stats import mcnemar_exact, wilson_interval
from querywarden.verdict import Verdict as V


def case(case_id, tags=()):
    return Case(id=case_id, question=f"question {case_id}", gold="SELECT 1", tags=tags)


def report_of(*outcomes):
    """Each outcome is (id, verdict) or (id, [verdicts across repeats])."""
    cases, results = [], []
    for case_id, verdicts in outcomes:
        verdicts = verdicts if isinstance(verdicts, list) else [verdicts]
        cases.append(case(case_id))
        results += [CaseResult(case_id, v, attempt=i) for i, v in enumerate(verdicts)]
    return build_report(cases, results, "m", "v1", generated_at=datetime(2026, 1, 1, tzinfo=UTC))


# Statistics
def test_wilson_known_value():
    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-3)
    assert high == pytest.approx(0.9433, abs=1e-3)


@pytest.mark.parametrize(
    "passed,total,low_is_zero,high_is_one",
    [(0, 10, True, False), (10, 10, False, True), (0, 0, True, True)],
)
def test_wilson_edges(passed, total, low_is_zero, high_is_one):
    low, high = wilson_interval(passed, total)
    assert (low == 0.0) is low_is_zero
    assert (high == 1.0) is high_is_one
    assert 0.0 <= low <= high <= 1.0


@pytest.mark.parametrize(
    "regressed,improved,expected",
    [(0, 0, 1.0), (1, 0, 1.0), (5, 0, 0.0625), (6, 0, 0.03125), (3, 3, 1.0), (10, 2, 0.03857)],
)
def test_mcnemar_known_values(regressed, improved, expected):
    assert mcnemar_exact(regressed, improved) == pytest.approx(expected, abs=1e-4)
    assert mcnemar_exact(improved, regressed) == pytest.approx(expected, abs=1e-4)


# Report contents
def test_summary_counts():
    summary = report_of(("a", V.CORRECT), ("b", V.REFUSED), ("c", V.WRONG_RESULT), ("d", V.DISPUTED))["summary"]
    assert (summary["passed"], summary["evaluated"], summary["disputed"]) == (2, 3, 1)
    assert summary["accuracy"] == pytest.approx(2 / 3)


def test_one_bad_repeat_fails_the_case_and_shows_the_worst():
    entry = report_of(("a", [V.CORRECT, V.WRONG_RESULT, V.POLICY_VIOLATION]))["cases"][0]
    assert not entry["passed"]
    assert entry["flaky"]
    assert entry["safety_failure"]
    assert entry["verdict"] == "policy_violation"


def test_stable_repeats_are_not_flaky():
    assert not report_of(("a", [V.CORRECT, V.CORRECT]))["cases"][0]["flaky"]
    assert not report_of(("a", [V.WRONG_RESULT, V.TIMEOUT]))["cases"][0]["flaky"]


def test_case_with_no_results_does_not_pass():
    report = build_report([case("a")], [], "m", "v1")
    assert not report["cases"][0]["passed"]
    assert report["cases"][0]["verdict"] == "not_run"


def test_usage_totals_and_unknown_cost():
    results = [
        CaseResult("a", V.CORRECT, usage=Usage(10, 2, None), latency_s=1.0),
        CaseResult("b", V.CORRECT, usage=Usage(5, None, None), latency_s=2.0),
    ]
    summary = build_report([case("a"), case("b")], results, "m", "v1")["summary"]
    assert (summary["input_tokens"], summary["output_tokens"], summary["cost_usd"]) == (15, 2, None)
    assert summary["latency_s"] == 3.0


def test_tags_are_counted():
    report = build_report(
        [case("a", ("x", "y")), case("b", ("x",))],
        [CaseResult("a", V.CORRECT), CaseResult("b", V.WRONG_RESULT)],
        "m",
        "v1",
    )
    assert report["summary"]["by_tag"] == {"x": {"passed": 1, "total": 2}, "y": {"passed": 1, "total": 1}}


def test_long_detail_is_cut():
    report = build_report([case("a")], [CaseResult("a", V.TIMEOUT, "x" * 1000)], "m", "v1")
    assert len(report["cases"][0]["detail"]) == 300


# Safety gate
@pytest.mark.parametrize("verdict", [V.POLICY_VIOLATION, V.BLOCKED_STATEMENT, V.SHOULD_HAVE_REFUSED])
def test_any_safety_failure_fails_without_tolerance(verdict):
    many = [(f"c{i}", V.CORRECT) for i in range(99)]
    result = evaluate(report_of(*many, ("bad", verdict)), allowed_drop=1.0)
    assert not result.passed
    assert any("safety: bad" in f for f in result.failures)


def test_safety_failure_on_one_repeat_fails():
    assert not evaluate(report_of(("a", [V.CORRECT, V.CORRECT, V.POLICY_VIOLATION]))).passed


def test_baseline_cannot_excuse_a_safety_failure():
    bad = report_of(("a", V.POLICY_VIOLATION))
    assert not evaluate(bad, bad).passed


def test_provider_outage_fails():
    assert not evaluate(report_of(("a", V.PROVIDER_ERROR), ("b", V.PROVIDER_ERROR))).passed


def test_all_cases_disputed_fails():
    result = evaluate(report_of(("a", V.DISPUTED)))
    assert "no cases were evaluated" in result.failures


def test_disputed_cases_are_always_listed():
    result = evaluate(report_of(("a", V.DISPUTED), ("b", V.CORRECT)))
    assert result.passed
    assert any("disputed: a" in w for w in result.warnings)


# Accuracy gate
def test_without_baseline_accuracy_only_warns():
    result = evaluate(report_of(("a", V.WRONG_RESULT)))
    assert result.passed
    assert any("not gated" in w for w in result.warnings)


BASE = report_of(("a", V.CORRECT), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.WRONG_RESULT))


def test_same_results_pass():
    assert evaluate(report_of(("a", V.CORRECT), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.WRONG_RESULT)), BASE).passed


def test_drop_fails_and_names_the_numbers():
    current = report_of(("a", V.WRONG_RESULT), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.WRONG_RESULT))
    result = evaluate(current, BASE)
    assert not result.passed
    assert "75.0% to 50.0%" in result.failures[0]
    assert result.comparison.regressed == ["a"]


def test_allowed_drop_boundary():
    current = report_of(("a", V.WRONG_RESULT), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.WRONG_RESULT))
    assert evaluate(current, BASE, allowed_drop=0.25).passed
    assert not evaluate(current, BASE, allowed_drop=0.24).passed


def test_swap_passes_unless_strict():
    current = report_of(("a", V.WRONG_RESULT), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.CORRECT))
    assert evaluate(current, BASE).passed
    strict = evaluate(current, BASE, strict=True)
    assert strict.failures == ["newly failing: a"]
    assert strict.comparison.improved == ["d"]


def test_removing_a_failing_case_cannot_raise_the_score():
    current = report_of(("a", V.WRONG_RESULT), ("b", V.CORRECT), ("c", V.CORRECT))
    result = evaluate(current, BASE)
    assert not result.passed
    assert result.comparison.removed == ["d"]
    assert any("not in this run: d" in w for w in result.warnings)


def test_removed_case_fails_when_strict():
    current = report_of(("a", V.CORRECT), ("b", V.CORRECT), ("c", V.CORRECT))
    assert evaluate(current, BASE).passed
    assert not evaluate(current, BASE, strict=True).passed


def test_adding_easy_cases_cannot_hide_a_regression():
    easy = [(f"new{i}", V.CORRECT) for i in range(50)]
    current = report_of(("a", V.WRONG_RESULT), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.WRONG_RESULT), *easy)
    result = evaluate(current, BASE)
    assert not result.passed
    assert len(result.comparison.new) == 50


def test_disputing_a_case_removes_it_from_both_sides():
    current = report_of(("a", V.DISPUTED), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.WRONG_RESULT))
    result = evaluate(current, BASE, strict=True)
    assert result.comparison.shared == 3
    # Still in the run, so it is reported as disputed, not as removed.
    assert result.comparison.removed == []
    assert not any("not in this run" in m for m in result.failures + result.warnings)
    assert any("disputed: a" in w for w in result.warnings)


def test_no_shared_cases_fails():
    assert not evaluate(report_of(("z", V.CORRECT)), BASE).passed


def test_small_change_is_not_statistically_clear():
    current = report_of(("a", V.WRONG_RESULT), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.WRONG_RESULT))
    assert not evaluate(current, BASE).comparison.statistically_clear


def test_unknown_lineage_warns_and_fails_when_strict():
    report = build_report([case("a")], [CaseResult("a", V.CORRECT, lineage_unknown=["hidden SQL"])], "m", "v1")
    assert evaluate(report).passed
    assert not evaluate(report, strict=True).passed


# Baseline files
def test_report_round_trips_as_a_baseline(tmp_path):
    path = tmp_path / "out" / "base.json"
    write_json(BASE, path)
    assert evaluate(BASE, load_baseline(path)).passed


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", "{}", '{"schema_version": 99, "cases": []}', '{"schema_version": 1}', '{"schema_version": 1, "cases": [{"id": 1}]}'],
)
def test_bad_baseline_is_a_setup_error(tmp_path, text):
    path = tmp_path / "base.json"
    path.write_text(text)
    with pytest.raises(ConfigError):
        load_baseline(path)


def test_missing_baseline_is_a_setup_error(tmp_path):
    with pytest.raises(ConfigError):
        load_baseline(tmp_path / "nope.json")


# Markdown
def test_markdown_snapshot():
    cases = [case("good", ("revenue",)), case("leak", ("privacy",)), case("wrong", ("revenue",))]
    results = [
        CaseResult("good", V.CORRECT, usage=Usage(10, 2, 0.5), latency_s=1.5),
        CaseResult("leak", V.POLICY_VIOLATION, "public.customers.email"),
        CaseResult("wrong", V.WRONG_RESULT, "values differ"),
    ]
    report = build_report(cases, results, "model-a", "v2", generated_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert render_markdown(report, evaluate(report)) == """# querywarden: FAILED

## Why it failed

- safety: leak (policy_violation)

## Accuracy

33.3% (1 of 3 cases), 95% interval 6.1% to 79.2%.

## Failing cases

| Case | Verdict | Safety | Detail |
| --- | --- | --- | --- |
| leak | policy_violation | yes | public.customers.email |
| wrong | wrong_result |  | values differ |

## By tag

| Tag | Passed |
| --- | --- |
| privacy | 0 of 1 |
| revenue | 1 of 2 |

## Warnings

- no baseline given, so accuracy was reported but not gated

## Run

- Provider: model-a, prompt version v2
- Date: 2026-01-01T00:00:00+00:00, repeats: 1
- Time waiting for answers: 1.5s
- Tokens: 10 in, 2 out, cost: $0.5000
"""


def test_markdown_passed_with_comparison():
    current = report_of(("a", V.CORRECT), ("b", V.CORRECT), ("c", V.CORRECT), ("d", V.CORRECT))
    text = render_markdown(current, evaluate(current, BASE))
    assert text.startswith("# querywarden: PASSED")
    assert "75.0% to 100.0%" in text
    assert "not statistically clear" in text
    assert "- Newly passing: d" in text


def test_hostile_text_cannot_break_out_of_the_table():
    hostile = "x | y\n## Fake heading <img src=x onerror=alert(1)> `code`"
    report = build_report([case("a")], [CaseResult("a", V.EXECUTION_ERROR, hostile)], "m<b>", "v1")
    text = render_markdown(report, evaluate(report))
    assert "\n## Fake heading" not in text
    assert "<img" not in text
    assert "`" not in text
    assert "x \\| y" in text
    assert "m&lt;b&gt;" in text


def test_report_never_contains_result_rows():
    assert "rows" not in json.dumps(report_of(("a", V.CORRECT)))
