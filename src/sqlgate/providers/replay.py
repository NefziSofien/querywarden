"""Record answers once, then replay them with no network and no API key.

Only the question, a version label and the answer are stored, never credentials or configuration.
"""

import hashlib
import json
from pathlib import Path

from sqlgate.providers.base import Answer, Provider, ProviderError, parse_answer


class RecordingError(ProviderError):
    """The recording file itself is broken. Replaying a partial file would give misleading scores."""


def recording_key(question: str, provider_id: str, prompt_version: str, attempt: int) -> str:
    identity = json.dumps([question, provider_id, prompt_version, attempt], ensure_ascii=False)
    return hashlib.sha256(identity.encode()).hexdigest()


class RecordingStore:
    """A JSON Lines file, sorted by key so that re-recording gives small, readable diffs."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._records = self._load()

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        records = {}
        for number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                records[record["key"]] = record
            except (ValueError, KeyError, TypeError) as e:
                raise RecordingError(f"{self._path}:{number} is not a valid recording") from e
        return records

    def get(self, key: str) -> dict | None:
        return self._records.get(key)

    def put(self, key: str, record: dict) -> None:
        self._records[key] = {"key": key, **record}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(self._records[k], ensure_ascii=False, sort_keys=True) for k in sorted(self._records)]
        # Write then rename, so an interrupted run never leaves half a file.
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(self._path)


class RecordingProvider:
    def __init__(self, inner: Provider, store: RecordingStore, provider_id: str, prompt_version: str):
        self._inner = inner
        self._store = store
        self._provider_id = provider_id
        self._prompt_version = prompt_version

    def ask(self, question: str, attempt: int = 0) -> Answer:
        answer = self._inner.ask(question, attempt)
        key = recording_key(question, self._provider_id, self._prompt_version, attempt)
        self._store.put(
            key,
            {
                "question": question,
                "provider": self._provider_id,
                "prompt_version": self._prompt_version,
                "attempt": attempt,
                "answer": answer.to_dict(),
            },
        )
        return answer


class ReplayProvider:
    def __init__(self, store: RecordingStore, provider_id: str, prompt_version: str):
        self._store = store
        self._provider_id = provider_id
        self._prompt_version = prompt_version

    def ask(self, question: str, attempt: int = 0) -> Answer:
        record = self._store.get(recording_key(question, self._provider_id, self._prompt_version, attempt))
        if record is None:
            raise ProviderError(
                f"no recording for this question (provider {self._provider_id}, "
                f"prompt_version {self._prompt_version}, attempt {attempt}); record it first"
            )
        try:
            return parse_answer(record["answer"])
        except KeyError as e:
            raise RecordingError("recording has no answer") from e
