"""The contract between sqlgate and the system under test. Every answer is untrusted input."""

import math
from dataclasses import asdict, dataclass
from typing import Any, Protocol

MAX_SQL_CHARS = 100_000
_USAGE_FIELDS = ("input_tokens", "output_tokens", "cost_usd")


class ProviderError(Exception):
    """The system gave no usable answer. This becomes a provider_error verdict, not a crash."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class Answer:
    sql: str | None = None
    refused: bool = False
    usage: Usage = Usage()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Provider(Protocol):
    def ask(self, question: str, attempt: int = 0) -> Answer: ...


def parse_answer(data: Any) -> Answer:
    """Exactly one of a SQL string or a refusal. Anything else is a broken answer."""
    if not isinstance(data, dict):
        raise ProviderError(f"expected a JSON object, got {type(data).__name__}")

    refused = data.get("refused", False)
    if not isinstance(refused, bool):
        raise ProviderError("refused must be true or false")

    sql = data.get("sql")
    if refused:
        if sql:
            raise ProviderError("an answer cannot both refuse and contain SQL")
        return Answer(refused=True, usage=_parse_usage(data.get("usage")))

    if not isinstance(sql, str) or not sql.strip():
        raise ProviderError("sql must be a non-empty string unless refused is true")
    if len(sql) > MAX_SQL_CHARS:
        raise ProviderError(f"sql is longer than {MAX_SQL_CHARS} characters")
    return Answer(sql=sql, usage=_parse_usage(data.get("usage")))


def _parse_usage(data: Any) -> Usage:
    """Usage is optional reporting, so a bad value is dropped instead of failing the case."""
    if not isinstance(data, dict):
        return Usage()
    return Usage(**{name: _number(data.get(name)) for name in _USAGE_FIELDS})


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None
