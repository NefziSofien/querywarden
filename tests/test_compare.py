from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sqlgate.compare import Options, Outcome, compare
from sqlgate.executor import ExecutionResult, Status

MATCH, SHAPE, WRONG = Outcome.MATCH, Outcome.WRONG_SHAPE, Outcome.WRONG_RESULT


def result(columns, rows, truncated=False):
    return ExecutionResult(Status.OK, list(columns), [tuple(r) for r in rows], truncated)


CASES = {
    # Rows
    "same rows": (["a"], [[1], [2]], ["a"], [[1], [2]], {}, MATCH),
    "row order ignored by default": (["a"], [[1], [2]], ["a"], [[2], [1]], {}, MATCH),
    "row order enforced when ordered": (["a"], [[1], [2]], ["a"], [[2], [1]], {"ordered": True}, WRONG),
    "ordered and same order": (["a"], [[1], [2]], ["a"], [[1], [2]], {"ordered": True}, MATCH),
    "duplicates are kept": (["a"], [[1], [1], [2]], ["a"], [[1], [2], [2]], {}, WRONG),
    "missing duplicate": (["a"], [[1], [1]], ["a"], [[1]], {}, WRONG),
    "values differ": (["a"], [[1]], ["a"], [[2]], {}, WRONG),
    "both empty": (["a"], [], ["b"], [], {}, MATCH),
    "empty against rows": (["a"], [], ["a"], [[1]], {}, WRONG),
    "values stay in their row": (["a", "b"], [[1, 2], [3, 4]], ["a", "b"], [[1, 4], [3, 2]], {}, WRONG),
    # NULL
    "null equals null": (["a"], [[None]], ["a"], [[None]], {}, MATCH),
    "null is not zero": (["a"], [[None]], ["a"], [[0]], {}, WRONG),
    "null is not empty text": (["a"], [[None]], ["a"], [[""]], {}, WRONG),
    "null is not the text null": (["a"], [[None]], ["a"], [["null"]], {}, WRONG),
    "null among floats": (["a"], [[None]], ["a"], [[0.0]], {}, WRONG),
    "float column keeps nulls": (["a"], [[None], [1.0]], ["a"], [[1.0], [None]], {}, MATCH),
    # Numbers
    "decimal scale ignored": (["a"], [[Decimal("10.50")]], ["a"], [[Decimal("10.5")]], {}, MATCH),
    "decimal is exact": (["a"], [[Decimal("0.1000001")]], ["a"], [[Decimal("0.1")]], {}, WRONG),
    "integer equals decimal": (["a"], [[10]], ["a"], [[Decimal("10")]], {}, MATCH),
    "float within tolerance": (["a"], [[0.1 + 0.2]], ["a"], [[0.3]], {}, MATCH),
    "float outside tolerance": (["a"], [[1.0]], ["a"], [[1.001]], {}, WRONG),
    "custom tolerance": (["a"], [[1.0]], ["a"], [[1.001]], {"float_tolerance": 0.01}, MATCH),
    "large float sums": (["a"], [[1e12]], ["a"], [[1e12 + 0.001]], {}, MATCH),
    "float against decimal": (["a"], [[Decimal("10.5")]], ["a"], [[10.5]], {}, MATCH),
    "nan equals nan": (["a"], [[float("nan")]], ["a"], [[float("nan")]], {}, MATCH),
    "nan is not a number": (["a"], [[float("nan")]], ["a"], [[1.0]], {}, WRONG),
    "unordered floats": (["a"], [[1.0], [2.0]], ["a"], [[2.0000000001], [1.0]], {}, MATCH),
    "float rows grouped by exact column": (
        ["k", "v"], [["x", 1.0], ["y", 2.0]], ["k", "v"], [["x", 2.0], ["y", 1.0]], {}, WRONG,
    ),
    # Types do not collide
    "boolean is not one": (["a"], [[True]], ["a"], [[1]], {}, WRONG),
    "boolean equals boolean": (["a"], [[False]], ["a"], [[False]], {}, MATCH),
    "text is not a number": (["a"], [["1"]], ["a"], [[1]], {}, WRONG),
    "text is case sensitive": (["a"], [["Ada"]], ["a"], [["ada"]], {}, WRONG),
    "trailing space matters": (["a"], [["Ada"]], ["a"], [["Ada "]], {}, WRONG),
    "date is not a timestamp": (["a"], [[date(2025, 1, 1)]], ["a"], [[datetime(2025, 1, 1)]], {}, WRONG),
    "same instant in two time zones": (
        ["a"],
        [[datetime(2025, 1, 1, 12, tzinfo=timezone.utc)]],
        ["a"],
        [[datetime(2025, 1, 1, 13, tzinfo=timezone(timedelta(hours=1)))]],
        {},
        MATCH,
    ),
    "arrays": (["a"], [[[1, 2]]], ["a"], [[[1, 2]]], {}, MATCH),
    "array order matters": (["a"], [[[1, 2]]], ["a"], [[[2, 1]]], {}, WRONG),
    "json key order ignored": (["a"], [[{"x": 1, "y": 2}]], ["a"], [[{"y": 2, "x": 1}]], {}, MATCH),
    "bytes": (["a"], [[b"x"]], ["a"], [[memoryview(b"x")]], {}, MATCH),
    # Columns
    "aliases ignored": (["revenue"], [[1]], ["total_sales"], [[1]], {}, MATCH),
    "column order ignored": (["a", "b"], [[1, "x"], [2, "y"]], ["b", "a"], [["x", 1], ["y", 2]], {}, MATCH),
    "swapped same-type columns": (["a", "b"], [[1, 2], [3, 4]], ["b", "a"], [[2, 1], [4, 3]], {}, MATCH),
    "wrongly paired columns": (["a", "b"], [[1, 2], [3, 4]], ["a", "b"], [[1, 4], [3, 2]], {}, WRONG),
    "same values need the right assignment": (
        ["a", "b"], [[1, 1], [2, 2], [1, 2]], ["x", "y"], [[1, 2], [1, 1], [2, 2]], {}, MATCH,
    ),
    "duplicate candidate column cannot fill two": (["a", "b"], [[1, 2]], ["a", "a2"], [[1, 1]], {}, WRONG),
    "ordered with swapped columns": (["a", "b"], [[1, "x"], [2, "y"]], ["b", "a"], [["x", 1], ["y", 2]], {"ordered": True}, MATCH),
    "missing column": (["a", "b"], [[1, 2]], ["a"], [[1]], {}, SHAPE),
    "extra column fails": (["a"], [[1]], ["a", "b"], [[1, 2]], {}, SHAPE),
    "extra column allowed": (["a"], [[1]], ["b", "a"], [[2, 1]], {"allow_extra_columns": True}, MATCH),
    "extra column allowed but value wrong": (["a"], [[1]], ["a", "b"], [[2, 3]], {"allow_extra_columns": True}, WRONG),
    "missing column with extras allowed": (["a", "b"], [[1, 2]], ["a"], [[1]], {"allow_extra_columns": True}, SHAPE),
}


@pytest.mark.parametrize("name", CASES)
def test_comparison_rules(name):
    gold_columns, gold_rows, columns, rows, options, expected = CASES[name]
    outcome = compare(result(gold_columns, gold_rows), result(columns, rows), Options(**options)).outcome
    assert outcome is expected


@pytest.mark.parametrize("gold_truncated,candidate_truncated", [(True, False), (False, True), (True, True)])
def test_truncated_results_are_never_confirmed(gold_truncated, candidate_truncated):
    gold = result(["a"], [[1]], gold_truncated)
    candidate = result(["a"], [[1]], candidate_truncated)
    assert compare(gold, candidate).outcome is WRONG


def test_many_identical_columns_finish_quickly():
    row = [1] * 12
    assert compare(result(map(str, range(12)), [row]), result(map(str, range(12)), [row])).outcome is MATCH


def test_impossible_assignment_over_many_columns_stops_at_the_limit():
    gold = result(map(str, range(12)), [[1] * 12, [2] * 12, [1] * 11 + [2]])
    candidate = result(map(str, range(12)), [[2] * 12, [1] * 12, [2] * 11 + [1]])
    assert compare(gold, candidate).outcome is WRONG


def test_rows_separated_by_sorting_still_match():
    gold = [[1.0, 2.0], [1.0000001, 1.0]]
    candidate = [[1.0000001, 2.0], [1.0, 1.0]]
    assert compare(result("ab", gold), result("ab", candidate)).outcome is MATCH


def test_large_result_is_fast_enough():
    rows = [[i, f"name-{i}", i * 0.5] for i in range(10_000)]
    assert compare(result("abc", rows), result("abc", list(reversed(rows)))).outcome is MATCH
