"""Call your own endpoint, or any OpenAI-compatible API."""

import json
import re
import time
from typing import Any

import httpx

from sqlgate.providers.base import Answer, ProviderError, parse_answer

_RETRYABLE = {429, 500, 502, 503, 504}
_MAX_ERROR_CHARS = 500
_SQL_FENCE = re.compile(r"```(?:sql|postgresql|postgres)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_STARTS_LIKE_SQL = re.compile(r"\s*(SELECT|WITH|VALUES|TABLE|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE)\b", re.IGNORECASE)


class _JsonPoster:
    def __init__(self, url: str, headers: dict[str, str], timeout_s: float, retries: int, client: httpx.Client | None):
        self._url = url
        self._headers = headers
        self._retries = retries
        # Redirects stay off, so an API key is never forwarded to another host.
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=False)

    def post(self, body: dict[str, Any]) -> Any:
        for attempt in range(self._retries + 1):
            retry = attempt < self._retries
            try:
                response = self._client.post(self._url, json=body, headers=self._headers)
            except httpx.TimeoutException as e:
                if retry:
                    continue
                raise ProviderError("request timed out") from e
            except httpx.HTTPError as e:
                if retry:
                    continue
                raise ProviderError(f"request failed: {type(e).__name__}") from e
            if response.status_code in _RETRYABLE and retry:
                time.sleep(min(2**attempt, 8))
                continue
            if response.status_code != 200:
                raise ProviderError(f"HTTP {response.status_code}: {self._redact(response.text)}")
            try:
                return response.json()
            except ValueError as e:
                raise ProviderError("response was not valid JSON") from e
        raise ProviderError("no response")

    def _redact(self, text: str) -> str:
        for value in self._headers.values():
            secret = value.removeprefix("Bearer ")
            text = text.replace(secret, "[redacted]")
        return text[:_MAX_ERROR_CHARS]


class HttpProvider:
    """POST {"question", "attempt"} to your endpoint, which replies with the answer contract."""

    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout_s: float = 60, retries: int = 2, client: httpx.Client | None = None):
        self._poster = _JsonPoster(url, headers or {}, timeout_s, retries, client)

    def ask(self, question: str, attempt: int = 0) -> Answer:
        return parse_answer(self._poster.post({"question": question, "attempt": attempt}))


class OpenAICompatibleProvider:
    """Calls a chat completions API. The system prompt is yours: sqlgate only reads the reply."""

    def __init__(
        self,
        base_url: str,
        model: str,
        system_prompt: str,
        api_key: str | None = None,
        temperature: float = 0,
        timeout_s: float = 120,
        retries: int = 2,
        client: httpx.Client | None = None,
    ):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._poster = _JsonPoster(f"{base_url.rstrip('/')}/chat/completions", headers, timeout_s, retries, client)
        self._model = model
        self._system_prompt = system_prompt
        self._temperature = temperature

    def ask(self, question: str, attempt: int = 0) -> Answer:
        data = self._poster.post(
            {
                "model": self._model,
                "temperature": self._temperature,
                "messages": [
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": question},
                ],
            }
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError("response has no message content") from e
        if not isinstance(content, str):
            raise ProviderError("message content is not text")

        usage = data.get("usage") if isinstance(data, dict) else None
        return parse_answer({**extract_answer(content), "usage": _openai_usage(usage)})


def extract_answer(content: str) -> dict[str, Any]:
    """A JSON answer, fenced SQL or bare SQL. Text with no SQL at all counts as a refusal."""
    try:
        parsed = json.loads(content)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and ("sql" in parsed or "refused" in parsed):
        return {k: parsed[k] for k in ("sql", "refused") if k in parsed}

    fenced = _SQL_FENCE.search(content)
    if fenced:
        return {"sql": fenced.group(1).strip()}
    if _STARTS_LIKE_SQL.match(content):
        return {"sql": content.strip()}
    return {"refused": True}


def _openai_usage(usage: Any) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {}
    return {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}
