"""Scan returned rows for planted marker values, however the query produced them."""

import re
from collections.abc import Iterable
from typing import Any

from sqlgate.executor import ExecutionResult

_NOT_ALPHANUMERIC = re.compile(r"[\W_]+")


class BadCanary(ValueError):
    """A canary with no letters or digits would match every result."""


def find_canaries(result: ExecutionResult, canaries: Iterable[str]) -> list[str]:
    """Catches case changes, added separators, concatenation and reversal. Hashes and encodings get past this."""
    markers = {canary: _normalize(canary) for canary in canaries}
    for canary, marker in markers.items():
        if not marker:
            raise BadCanary(canary)
    if not markers:
        return []

    found = set()
    for row in result.rows:
        for cell in row:
            for text in _texts(cell):
                normalized = _normalize(text)
                found.update(c for c, m in markers.items() if m in normalized or m[::-1] in normalized)
    return sorted(found)


def _normalize(text: str) -> str:
    return _NOT_ALPHANUMERIC.sub("", text).casefold()


def _texts(value: Any) -> Iterable[str]:
    match value:
        case None | bool() | int() | float():
            return
        case str():
            yield value
        case bytes() | bytearray() | memoryview():
            yield bytes(value).decode("utf-8", errors="ignore")
        case dict():
            for key, item in value.items():
                yield from _texts(key)
                yield from _texts(item)
        case list() | tuple() | set():
            for item in value:
                yield from _texts(item)
        case _:
            yield str(value)
