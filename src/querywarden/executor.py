"""Run untrusted SQL under a restricted role, in a new read-only session for each query."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import psycopg
from psycopg import conninfo

from querywarden.statement import Check, check_statement

_BATCH_ROWS = 100
_DENIED = {"42501", "25006"}  # insufficient_privilege, read_only_sql_transaction
_TIMEOUT = {"57014", "55P03"}  # query_canceled, lock_not_available
_UNKNOWN_SCHEMA = {"42P01", "42703", "42883", "3F000"}  # table, column, function, schema


class Status(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"
    UNPARSED = "unparsed"
    TIMEOUT = "timeout"
    UNKNOWN_SCHEMA = "unknown_schema"
    ERROR = "error"


@dataclass(frozen=True)
class Limits:
    statement_timeout_ms: int = 5_000
    lock_timeout_ms: int = 1_000
    idle_timeout_ms: int = 10_000
    connect_timeout_s: int = 5
    max_rows: int = 10_000
    max_bytes: int = 10_000_000


@dataclass(frozen=True)
class ExecutionResult:
    status: Status
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    truncated: bool = False
    detail: str = ""
    sqlstate: str | None = None


class ConnectionFailed(Exception):
    """The database could not be reached. This is a setup problem, not a result for the query."""


class Executor:
    def __init__(self, dsn: str, limits: Limits = Limits()):
        self._limits = limits
        options = " ".join(
            f"-c {name}={value}"
            for name, value in {
                "statement_timeout": limits.statement_timeout_ms,
                "lock_timeout": limits.lock_timeout_ms,
                "idle_in_transaction_session_timeout": limits.idle_timeout_ms,
            }.items()
        )
        self._dsn = conninfo.make_conninfo(
            dsn,
            options=options,
            connect_timeout=limits.connect_timeout_s,
            application_name="querywarden",
        )

    def execute(self, sql: str) -> ExecutionResult:
        check = check_statement(sql)
        if check.outcome is Check.BLOCKED:
            return ExecutionResult(Status.BLOCKED, detail=check.reason)
        if check.outcome is Check.UNPARSED:
            return ExecutionResult(Status.UNPARSED, detail=check.reason)
        return self._execute_on_server(sql)

    def _execute_on_server(self, sql: str) -> ExecutionResult:
        # A server-side cursor only accepts one SELECT, so the server also rejects what the
        # static check might miss. A new connection means no session state survives a query.
        try:
            conn = psycopg.connect(self._dsn)
        except psycopg.OperationalError as e:
            raise ConnectionFailed(str(e)) from e

        try:
            conn.read_only = True
            with conn.cursor(name="querywarden") as cur:
                cur.execute(sql)
                result = self._fetch(cur)
            conn.rollback()
            return result
        except psycopg.Error as e:
            return _from_error(e)
        finally:
            conn.close()

    def _fetch(self, cur: psycopg.ServerCursor) -> ExecutionResult:
        columns = [c.name for c in cur.description or []]
        rows: list[tuple[Any, ...]] = []
        size = 0
        while batch := cur.fetchmany(_BATCH_ROWS):
            for row in batch:
                size += sum(len(str(v)) for v in row)
                if len(rows) >= self._limits.max_rows or size > self._limits.max_bytes:
                    return ExecutionResult(Status.OK, columns, rows, truncated=True)
                rows.append(row)
        return ExecutionResult(Status.OK, columns, rows)


def _from_error(e: psycopg.Error) -> ExecutionResult:
    state = e.sqlstate
    if state in _DENIED:
        status = Status.BLOCKED
    elif state in _TIMEOUT:
        status = Status.TIMEOUT
    elif state in _UNKNOWN_SCHEMA:
        status = Status.UNKNOWN_SCHEMA
    else:
        status = Status.ERROR
    # Never the server's message: it can quote a value the query read, as in
    # 'invalid input syntax for type integer: "ada@example.test"', and reports end up in PR comments.
    detail = f"{type(e).__name__} (SQLSTATE {state})" if state else type(e).__name__
    return ExecutionResult(status, detail=detail, sqlstate=state)
