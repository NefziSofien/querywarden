"""Different SQL with the same meaning must match, using the types PostgreSQL really returns."""

import pytest

from sqlgate.compare import Options, Outcome, compare
from sqlgate.executor import Executor, Status

EQUIVALENT = [
    (
        "SELECT customer_id, sum(total) FROM reporting.orders GROUP BY 1",
        "SELECT sum(total) AS revenue, customer_id AS who FROM reporting.orders GROUP BY customer_id",
    ),
    (
        "SELECT customer_id, sum(total) FROM reporting.orders GROUP BY 1",
        "WITH t AS (SELECT customer_id c, total FROM reporting.orders) SELECT c, sum(total) FROM t GROUP BY c ORDER BY 2 DESC",
    ),
    ("SELECT avg(total) FROM reporting.orders", "SELECT avg(total)::float8 FROM reporting.orders"),
    ("SELECT count(*) FROM reporting.orders", "SELECT sum(1) FROM reporting.orders"),
    ("SELECT '2025-01-01 12:00+00'::timestamptz", "SELECT '2025-01-01 13:00+01'::timestamptz"),
    ("SELECT '{\"a\": 1, \"b\": 2}'::jsonb", "SELECT '{\"b\": 2, \"a\": 1}'::jsonb"),
    ("SELECT ARRAY[1, 2]", "SELECT ARRAY[1, 2]::bigint[]"),
    ("SELECT '\\x01'::bytea", "SELECT decode('01', 'hex')"),
    ("SELECT '00000000-0000-0000-0000-000000000001'::uuid", "SELECT '00000000-0000-0000-0000-000000000001'::uuid"),
    ("SELECT interval '1 day'", "SELECT interval '24 hours' + interval '0 day'"),
    ("SELECT NULL::int", "SELECT NULL::text"),
]
DIFFERENT = [
    ("SELECT sum(total) FROM reporting.orders", "SELECT avg(total) FROM reporting.orders"),
    ("SELECT customer_id FROM reporting.orders", "SELECT DISTINCT customer_id FROM reporting.orders"),
    ("SELECT '2025-01-01'::date", "SELECT '2025-01-01'::timestamp"),
    ("SELECT true", "SELECT 1"),
    ("SELECT 1", "SELECT '1'"),
]


@pytest.fixture(scope="module")
def executor(db):
    return Executor(db.reader_dsn)


def run(executor, sql):
    result = executor.execute(sql)
    assert result.status is Status.OK, result
    return result


@pytest.mark.parametrize("gold,candidate", EQUIVALENT)
def test_equivalent_queries_match(executor, gold, candidate):
    assert compare(run(executor, gold), run(executor, candidate)).outcome is Outcome.MATCH


@pytest.mark.parametrize("gold,candidate", DIFFERENT)
def test_different_queries_do_not_match(executor, gold, candidate):
    assert compare(run(executor, gold), run(executor, candidate)).outcome is not Outcome.MATCH


def test_ordered_case_on_real_rows(executor):
    gold = run(executor, "SELECT id FROM reporting.orders ORDER BY total DESC")
    reversed_rows = run(executor, "SELECT id FROM reporting.orders ORDER BY total ASC")
    assert compare(gold, reversed_rows, Options(ordered=True)).outcome is Outcome.WRONG_RESULT
    assert compare(gold, reversed_rows).outcome is Outcome.MATCH
