"""Call any executable: JSON on stdin, JSON on stdout."""

import json
import subprocess
from collections.abc import Sequence

from querywarden.providers.base import Answer, ProviderError, parse_answer

_MAX_ERROR_CHARS = 500


class CommandProvider:
    def __init__(self, argv: Sequence[str], timeout_s: float = 60):
        if not argv:
            raise ValueError("the command is empty")
        self._argv = list(argv)
        self._timeout_s = timeout_s

    def ask(self, question: str, attempt: int = 0) -> Answer:
        request = json.dumps({"question": question, "attempt": attempt})
        try:
            # A list of arguments and no shell, so a question can never become a command.
            done = subprocess.run(
                self._argv, input=request, capture_output=True, text=True, timeout=self._timeout_s, check=False
            )
        except subprocess.TimeoutExpired as e:
            raise ProviderError(f"command timed out after {self._timeout_s}s") from e
        except OSError as e:
            raise ProviderError(f"command could not start: {e}") from e

        if done.returncode != 0:
            raise ProviderError(f"command exited with {done.returncode}: {done.stderr[:_MAX_ERROR_CHARS]}")
        try:
            return parse_answer(json.loads(done.stdout))
        except json.JSONDecodeError as e:
            raise ProviderError(f"command did not print valid JSON: {e}") from e
