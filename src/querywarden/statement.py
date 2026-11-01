"""Static check of generated SQL. It explains failures; the database is the real boundary."""

from dataclasses import dataclass
from enum import StrEnum

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

_FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,
    exp.Set,
    exp.Into,
    exp.Lock,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)


class Check(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    UNPARSED = "unparsed"


@dataclass(frozen=True)
class StatementCheck:
    outcome: Check
    reason: str = ""


def check_statement(sql: str) -> StatementCheck:
    """Allow exactly one read-only query. SQL that cannot be parsed is never executed."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
    except (ParseError, TokenError) as e:
        return StatementCheck(Check.UNPARSED, str(e).splitlines()[0])

    if len(statements) != 1:
        return StatementCheck(Check.BLOCKED, f"expected one statement, found {len(statements)}")

    root = statements[0]
    if not isinstance(root, (exp.Query, exp.Values)):
        return StatementCheck(Check.BLOCKED, f"{type(root).__name__} is not a read-only query")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return StatementCheck(Check.BLOCKED, f"contains {type(node).__name__}")

    return StatementCheck(Check.ALLOWED)
