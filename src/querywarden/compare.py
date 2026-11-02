"""Compare a query result to the gold result. SQL text and column names never matter."""

import json
import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from querywarden.executor import ExecutionResult

_REL_TOLERANCE = 1e-9  # float sums over large values drift beyond any absolute tolerance
_MAX_ASSIGNMENTS = 10_000
_MAX_PAIRWISE_ROWS = 1_000

Rows = Sequence[Sequence[Any]]


class Outcome(StrEnum):
    MATCH = "match"
    WRONG_SHAPE = "wrong_shape"
    WRONG_RESULT = "wrong_result"


@dataclass(frozen=True)
class Options:
    ordered: bool = False
    allow_extra_columns: bool = False
    float_tolerance: float = 1e-6


@dataclass(frozen=True)
class Comparison:
    outcome: Outcome
    detail: str = ""


def compare(gold: ExecutionResult, candidate: ExecutionResult, options: Options = Options()) -> Comparison:
    if gold.truncated or candidate.truncated:
        return Comparison(Outcome.WRONG_RESULT, "result was truncated, so it cannot be confirmed")

    gold_width, candidate_width = len(gold.columns), len(candidate.columns)
    if candidate_width < gold_width:
        return Comparison(Outcome.WRONG_SHAPE, f"expected {gold_width} columns, got {candidate_width}")
    if candidate_width > gold_width and not options.allow_extra_columns:
        return Comparison(Outcome.WRONG_SHAPE, f"expected {gold_width} columns, got {candidate_width}")

    if len(gold.rows) != len(candidate.rows):
        return Comparison(Outcome.WRONG_RESULT, f"expected {len(gold.rows)} rows, got {len(candidate.rows)}")

    assignment = _align_columns(gold, candidate, options)
    if assignment is None:
        return Comparison(Outcome.WRONG_RESULT, "values differ from the gold result")
    return Comparison(Outcome.MATCH)


def project_columns(gold: ExecutionResult, candidate: ExecutionResult, options: Options = Options()) -> list | None:
    """The candidate rows in the gold column order, or None when the shapes cannot be paired."""
    gold_width, candidate_width = len(gold.columns), len(candidate.columns)
    if candidate_width < gold_width or (candidate_width > gold_width and not options.allow_extra_columns):
        return None
    chosen = _align_columns(gold, candidate, options) if len(gold.rows) == len(candidate.rows) else None
    if chosen is None:
        # No alignment makes the values match, so pair same-named columns, then the rest in order.
        named: list[int | None] = []
        for name in gold.columns:
            named.append(next((j for j, c in enumerate(candidate.columns) if c == name and j not in named), None))
        free = iter([j for j in range(candidate_width) if j not in named])
        chosen = [j if j is not None else next(free) for j in named]
    return [tuple(row[j] for j in chosen) for row in candidate.rows]


def diff_rows(gold: Rows, candidate: Rows, options: Options = Options()) -> tuple[list, list]:
    """Rows only in the gold result and rows only in the candidate, under the same rules as compare.

    Both sides must already have the gold column order and width.
    """
    width = len(gold[0]) if gold else len(candidate[0]) if candidate else 0
    approximate = {
        i for i in range(width) if any(isinstance(row[i], float) for rows in (gold, candidate) for row in rows)
    }
    tolerance = options.float_tolerance
    if options.ordered:
        pairs = list(zip(gold, candidate))
        differ = [(g, c) for g, c in pairs if not _row_close(g, c, approximate, tolerance)]
        tail_gold, tail_candidate = list(gold[len(pairs) :]), list(candidate[len(pairs) :])
        return [g for g, _ in differ] + tail_gold, [c for _, c in differ] + tail_candidate
    if not approximate:
        remaining = Counter(_exact_key(row, ()) for row in candidate)
        only_gold = []
        for row in gold:
            key = _exact_key(row, ())
            if remaining[key]:
                remaining[key] -= 1
            else:
                only_gold.append(row)
        only_candidate = []
        for row in candidate:
            key = _exact_key(row, ())
            if remaining[key]:
                remaining[key] -= 1
                only_candidate.append(row)
        return only_gold, only_candidate

    remaining_rows = list(candidate)
    only_gold = []
    for row in gold:
        match = next((i for i, c in enumerate(remaining_rows) if _row_close(row, c, approximate, tolerance)), None)
        if match is None:
            only_gold.append(row)
        else:
            del remaining_rows[match]
    return only_gold, remaining_rows


def _align_columns(gold: ExecutionResult, candidate: ExecutionResult, options: Options) -> list[int] | None:
    """Find which candidate column stands for each gold column, since order and aliases are free."""
    width = len(gold.columns)
    gold_columns = [[row[i] for row in gold.rows] for i in range(width)]
    candidate_columns = [[row[i] for row in candidate.rows] for i in range(len(candidate.columns))]

    # Same-named columns first: the usual case resolves on the first try.
    options_per_column = []
    for i, gold_column in enumerate(gold_columns):
        matching = [j for j, column in enumerate(candidate_columns) if _column_matches(gold_column, column, options)]
        matching.sort(key=lambda j: candidate.columns[j] != gold.columns[i])
        if not matching:
            return None
        options_per_column.append(matching)

    attempts = 0

    def assign(position: int, chosen: list[int]) -> list[int] | None:
        nonlocal attempts
        if position == width:
            attempts += 1
            projected = [[row[j] for j in chosen] for row in candidate.rows]
            return list(chosen) if _rows_match(gold.rows, projected, options) else None
        for j in options_per_column[position]:
            if j in chosen:
                continue
            if attempts >= _MAX_ASSIGNMENTS:
                return None
            found = assign(position + 1, [*chosen, j])
            if found is not None:
                return found
        return None

    return assign(0, [])


def _column_matches(gold: list[Any], candidate: list[Any], options: Options) -> bool:
    return _rows_match([[v] for v in gold], [[v] for v in candidate], options)


def _rows_match(gold: Rows, candidate: Rows, options: Options) -> bool:
    if len(gold) != len(candidate):
        return False
    if not gold:
        return True

    width = len(gold[0])
    approximate = {
        i for i in range(width) if any(isinstance(row[i], float) for rows in (gold, candidate) for row in rows)
    }
    if options.ordered:
        return all(_row_close(g, c, approximate, options.float_tolerance) for g, c in zip(gold, candidate))
    if not approximate:
        return Counter(_exact_key(row, ()) for row in gold) == Counter(_exact_key(row, ()) for row in candidate)

    # Rows can only match inside a group that agrees on every exact column.
    gold_groups: dict[tuple, list[Sequence[Any]]] = defaultdict(list)
    candidate_groups: dict[tuple, list[Sequence[Any]]] = defaultdict(list)
    for row in gold:
        gold_groups[_exact_key(row, approximate)].append(row)
    for row in candidate:
        candidate_groups[_exact_key(row, approximate)].append(row)
    if gold_groups.keys() != candidate_groups.keys():
        return False
    return all(
        _group_matches(group, candidate_groups[key], approximate, options.float_tolerance)
        for key, group in gold_groups.items()
    )


def _group_matches(gold: list, candidate: list, approximate, tolerance: float) -> bool:
    if len(gold) != len(candidate):
        return False
    columns = sorted(approximate)
    by_values = lambda row: [_sort_key(row[i]) for i in columns]
    sorted_pairs = zip(sorted(gold, key=by_values), sorted(candidate, key=by_values))
    if all(_row_close(g, c, approximate, tolerance) for g, c in sorted_pairs):
        return True
    if len(gold) > _MAX_PAIRWISE_ROWS:
        return False

    # Sorting can separate rows when an earlier float differs within tolerance.
    remaining = list(candidate)
    for row in gold:
        match = next((i for i, c in enumerate(remaining) if _row_close(row, c, approximate, tolerance)), None)
        if match is None:
            return False
        del remaining[match]
    return True


def _sort_key(value: Any) -> tuple:
    if value is None:
        return (0, 0.0, "")
    if not _is_number(value):
        return (3, 0.0, str(value))
    number = float(value)
    return (1, 0.0, "") if math.isnan(number) else (2, number, "")


def _exact_key(row: Sequence[Any], approximate) -> tuple:
    return tuple(None if i in approximate else _canonical(v) for i, v in enumerate(row))


def _row_close(gold: Sequence[Any], candidate: Sequence[Any], approximate, tolerance: float) -> bool:
    return all(
        _approximately_equal(g, c, tolerance) if i in approximate else _canonical(g) == _canonical(c)
        for i, (g, c) in enumerate(zip(gold, candidate))
    )


def _approximately_equal(a: Any, b: Any, tolerance: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if not (_is_number(a) and _is_number(b)):
        return _canonical(a) == _canonical(b)
    x, y = float(a), float(b)
    if math.isnan(x) or math.isnan(y):
        return math.isnan(x) and math.isnan(y)
    return math.isclose(x, y, rel_tol=_REL_TOLERANCE, abs_tol=tolerance)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _canonical(value: Any) -> Any:
    """A hashable form where NULL, booleans, numbers and text never collide."""
    match value:
        case None:
            return ("null",)
        case bool():
            return ("bool", value)
        case Decimal() if value.is_nan():
            return ("nan",)
        case int() | Decimal():
            return ("number", value)
        case float():
            return ("nan",) if math.isnan(value) else ("number", value)
        case str():
            return ("text", value)
        case memoryview() | bytes() | bytearray():
            return ("bytes", bytes(value))
        case list() | tuple():
            return ("array", tuple(_canonical(v) for v in value))
        case dict():
            return ("json", json.dumps(value, sort_keys=True, default=str))
        case _:
            try:
                hash(value)
                return (type(value).__name__, value)
            except TypeError:
                return (type(value).__name__, str(value))
