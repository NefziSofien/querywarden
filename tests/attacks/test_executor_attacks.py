"""Attacks run against real PostgreSQL. Each one must fail and leave the database unchanged."""

from decimal import Decimal

import pytest

from querywarden.executor import ConnectionFailed, Executor, Limits, Status

WRITES = [
    "INSERT INTO reporting.orders VALUES (9, 1, 1)",
    "UPDATE reporting.orders SET total = 0",
    "DELETE FROM reporting.orders",
    "TRUNCATE public.orders",
    "DROP VIEW reporting.orders",
    "CREATE TABLE reporting.x (a int)",
    "CREATE TEMP TABLE x (a int)",
    "SELECT * INTO reporting.x FROM reporting.orders",
    "WITH d AS (DELETE FROM reporting.orders RETURNING *) SELECT * FROM d",
    "SELECT 1; DROP VIEW reporting.orders",
    "SELECT 1; COMMIT; DROP VIEW reporting.orders",
    "COMMIT; DROP VIEW reporting.orders",
    "SET TRANSACTION READ WRITE; DELETE FROM reporting.orders",
    "SET ROLE admin; DELETE FROM public.orders",
    "DO $$ BEGIN DELETE FROM public.orders; END $$",
    "COPY reporting.orders TO PROGRAM 'id'",
    "COPY public.customers TO STDOUT",
    "SELECT * FROM reporting.orders FOR UPDATE",
    "GRANT SELECT ON public.customers TO reader",
    "ALTER ROLE reader SUPERUSER",
]

# These pass the static check, so the role and the read-only session have to stop them.
READS_AND_SIDE_EFFECTS = [
    "SELECT email FROM public.customers",
    "SELECT * FROM public.customers",
    "SELECT o.id FROM reporting.orders o JOIN public.customers c ON c.id = o.customer_id",
    "SELECT * FROM public.orders",
    "SELECT query_to_xml('SELECT email FROM public.customers', true, true, '')",
    "SELECT query_to_xml('DELETE FROM public.orders RETURNING id', true, true, '')",
    "SELECT table_to_xml('public.customers', true, true, '')",
    "SELECT * FROM pg_authid",
    "SELECT * FROM pg_shadow",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_read_binary_file('/etc/passwd')",
    "SELECT pg_ls_dir('/')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT lo_create(0)",
    "SELECT nextval('public.invoice_seq')",
    "SELECT setval('public.invoice_seq', 99)",
    "SELECT set_config('transaction_read_only', 'off', true), * FROM public.orders",
    "SELECT set_config('role', 'admin', false)",
]

# Must be refused by the server alone, in case a parser bug lets them through.
SERVER_ONLY = [w for w in WRITES if "FOR UPDATE" not in w]


@pytest.fixture(scope="module")
def executor(db):
    return Executor(db.reader_dsn)


@pytest.mark.parametrize("sql", WRITES)
def test_writes_fail_and_change_nothing(db, executor, sql):
    before = db.snapshot()
    result = executor.execute(sql)
    assert result.status is Status.BLOCKED, result
    assert not result.rows
    assert db.snapshot() == before


@pytest.mark.parametrize("sql", READS_AND_SIDE_EFFECTS)
def test_privileged_reads_and_side_effects_fail(db, executor, sql):
    before = db.snapshot()
    result = executor.execute(sql)
    assert result.status is Status.BLOCKED, result
    assert not result.rows
    assert db.snapshot() == before


@pytest.mark.parametrize("sql", SERVER_ONLY)
def test_server_refuses_writes_without_the_static_check(db, executor, sql):
    before = db.snapshot()
    result = executor._execute_on_server(sql)
    assert result.status is not Status.OK, result
    assert db.snapshot() == before


def test_denied_access_is_reported_as_blocked(executor):
    result = executor.execute("SELECT email FROM public.customers")
    assert result.status is Status.BLOCKED
    assert result.sqlstate == "42501"


@pytest.mark.parametrize(
    "sql,status",
    [
        ("SELECT contact::int FROM reporting.customer_contacts", Status.ERROR),
        ("SELECT contact::regclass FROM reporting.customer_contacts", Status.UNKNOWN_SCHEMA),
        ("SELECT contact::date FROM reporting.customer_contacts", Status.ERROR),
    ],
)
def test_error_detail_never_quotes_a_value(executor, sql, status):
    # The server message would read: invalid input syntax for type integer: "canary-7f3a@example.test"
    result = executor.execute(sql)
    assert result.status is status
    assert "canary" not in result.detail and "@" not in result.detail
    assert result.sqlstate in result.detail


def test_session_state_does_not_survive_a_query(executor):
    assert executor.execute("SELECT set_config('statement_timeout', '0', false)").status is Status.OK
    assert executor.execute("SHOW statement_timeout").status is Status.BLOCKED
    assert executor.execute("SELECT current_setting('statement_timeout')").rows == [("5s",)]


def test_session_is_read_only_and_role_is_unchanged(executor):
    result = executor.execute("SELECT current_setting('transaction_read_only'), current_user, session_user")
    assert result.rows == [("on", "reader", "reader")]


def test_valid_query_returns_rows_with_exact_types(executor):
    result = executor.execute("SELECT id, total FROM reporting.orders WHERE id = 1")
    assert result.status is Status.OK
    assert result.columns == ["id", "total"]
    assert result.rows == [(1, Decimal("10.50"))]
    assert not result.truncated


def test_percent_signs_are_not_treated_as_parameters(executor):
    assert executor.execute("SELECT '100%s' AS a").rows == [("100%s",)]


def test_slow_query_times_out(db):
    result = Executor(db.reader_dsn, Limits(statement_timeout_ms=200)).execute("SELECT pg_sleep(5)")
    assert result.status is Status.TIMEOUT


def test_cross_join_bomb_times_out(db):
    bomb = "SELECT count(*) FROM generate_series(1, 100000000) a, generate_series(1, 100000000) b"
    assert Executor(db.reader_dsn, Limits(statement_timeout_ms=300)).execute(bomb).status is Status.TIMEOUT


def test_endless_rows_are_capped(db):
    result = Executor(db.reader_dsn, Limits(max_rows=500)).execute("SELECT generate_series(1, 100000000)")
    assert result.status is Status.OK
    assert result.truncated
    assert len(result.rows) == 500


def test_large_values_are_capped_by_bytes(db):
    result = Executor(db.reader_dsn, Limits(max_bytes=5_000)).execute(
        "SELECT repeat('x', 1000) FROM generate_series(1, 100)"
    )
    assert result.truncated
    assert len(result.rows) == 5


def test_killing_its_own_backend_is_only_an_error(db, executor):
    result = executor.execute("SELECT pg_terminate_backend(pg_backend_pid())")
    assert result.status is Status.ERROR
    assert executor.execute("SELECT 1").rows == [(1,)]


def test_unknown_table_is_a_schema_error(executor):
    assert executor.execute("SELECT * FROM reporting.nope").status is Status.UNKNOWN_SCHEMA


def test_unknown_column_is_a_schema_error(executor):
    assert executor.execute("SELECT nope FROM reporting.orders").status is Status.UNKNOWN_SCHEMA


def test_runtime_error_is_reported(executor):
    assert executor.execute("SELECT 1 / 0").status is Status.ERROR


def test_unreachable_database_raises(db):
    bad = Executor("host=127.0.0.1 port=1 dbname=test user=reader", Limits(connect_timeout_s=2))
    with pytest.raises(ConnectionFailed):
        bad.execute("SELECT 1")
