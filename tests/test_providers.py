import json
import sys
from pathlib import Path

import httpx
import pytest

from querywarden.providers import Answer, ProviderError, Usage, parse_answer
from querywarden.providers.command import CommandProvider
from querywarden.providers.http import HttpProvider, OpenAICompatibleProvider, extract_answer
from querywarden.providers.replay import RecordingError, RecordingProvider, RecordingStore, ReplayProvider, recording_key


# Contract
@pytest.mark.parametrize(
    "data,expected",
    [
        ({"sql": "SELECT 1"}, Answer(sql="SELECT 1")),
        ({"sql": "SELECT 1", "refused": False}, Answer(sql="SELECT 1")),
        ({"refused": True}, Answer(refused=True)),
        ({"refused": True, "sql": None}, Answer(refused=True)),
        ({"refused": True, "sql": ""}, Answer(refused=True)),
        (
            {"sql": "SELECT 1", "usage": {"input_tokens": 3, "output_tokens": 4, "cost_usd": 0.01}},
            Answer(sql="SELECT 1", usage=Usage(3, 4, 0.01)),
        ),
    ],
)
def test_valid_answers(data, expected):
    assert parse_answer(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "SELECT 1",
        {},
        {"sql": ""},
        {"sql": "   "},
        {"sql": 1},
        {"sql": ["SELECT 1"]},
        {"refused": "yes"},
        {"refused": 1, "sql": "SELECT 1"},
        {"refused": True, "sql": "SELECT 1"},
        {"sql": "x" * 100_001},
    ],
)
def test_broken_answers_are_rejected(data):
    with pytest.raises(ProviderError):
        parse_answer(data)


@pytest.mark.parametrize(
    "usage",
    [None, "lots", [], {"input_tokens": "3"}, {"input_tokens": -1}, {"cost_usd": float("nan")}, {"cost_usd": float("inf")}, {"input_tokens": True}],
)
def test_bad_usage_is_dropped_without_failing_the_answer(usage):
    assert parse_answer({"sql": "SELECT 1", "usage": usage}).usage == Usage()


# Command provider
def script(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "system.py"
    path.write_text("import json, sys, time\nrequest = json.load(sys.stdin)\n" + body)
    return [sys.executable, str(path)]


def test_command_receives_the_question_and_returns_the_answer(tmp_path):
    argv = script(tmp_path, "print(json.dumps({'sql': 'SELECT ' + repr(request['question']) + ' -- ' + str(request['attempt'])}))")
    assert CommandProvider(argv).ask("hello", attempt=2).sql == "SELECT 'hello' -- 2"


def test_question_never_reaches_a_shell(tmp_path):
    marker = tmp_path / "pwned"
    argv = script(tmp_path, "print(json.dumps({'refused': True}))")
    question = f"'; touch {marker}; $(touch {marker}) `touch {marker}`"
    assert CommandProvider(argv).ask(question).refused
    assert not marker.exists()


@pytest.mark.parametrize(
    "body,message",
    [
        ("sys.stderr.write('boom'); sys.exit(3)", "exited with 3: boom"),
        ("print('not json')", "valid JSON"),
        ("print('')", "valid JSON"),
        ("print(json.dumps({'sql': ''}))", "non-empty"),
        ("print(json.dumps(['SELECT 1']))", "JSON object"),
    ],
)
def test_command_failures_are_provider_errors(tmp_path, body, message):
    with pytest.raises(ProviderError, match=message):
        CommandProvider(script(tmp_path, body)).ask("q")


def test_command_timeout(tmp_path):
    with pytest.raises(ProviderError, match="timed out"):
        CommandProvider(script(tmp_path, "time.sleep(30)"), timeout_s=0.5).ask("q")


def test_missing_executable():
    with pytest.raises(ProviderError, match="could not start"):
        CommandProvider(["/nonexistent/querywarden-system"]).ask("q")


def test_empty_command_is_a_setup_error():
    with pytest.raises(ValueError):
        CommandProvider([])


# HTTP provider
def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_http_sends_question_and_parses_answer():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content), auth=request.headers.get("x-key"))
        return httpx.Response(200, json={"sql": "SELECT 1"})

    answer = HttpProvider("http://sut/ask", {"x-key": "k"}, client=client(handler)).ask("q", attempt=1)
    assert answer.sql == "SELECT 1"
    assert seen == {"question": "q", "attempt": 1, "auth": "k"}


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("querywarden.providers.http.time.sleep", lambda _: None)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_http_retries_temporary_failures(no_sleep, status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status) if len(calls) < 3 else httpx.Response(200, json={"refused": True})

    assert HttpProvider("http://sut", client=client(handler)).ask("q").refused
    assert len(calls) == 3


def test_http_gives_up_after_retries(no_sleep):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="down")

    with pytest.raises(ProviderError, match="HTTP 503: down"):
        HttpProvider("http://sut", retries=1, client=client(handler)).ask("q")
    assert len(calls) == 2


def test_http_does_not_retry_client_errors():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="nope")

    with pytest.raises(ProviderError, match="HTTP 401"):
        HttpProvider("http://sut", client=client(handler)).ask("q")
    assert len(calls) == 1


def test_http_connection_failure_and_timeout(no_sleep):
    def refuse(request):
        raise httpx.ConnectError("no route")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError, match="ConnectError"):
        HttpProvider("http://sut", retries=0, client=client(refuse)).ask("q")
    with pytest.raises(ProviderError, match="timed out"):
        HttpProvider("http://sut", retries=0, client=client(slow)).ask("q")


def test_http_invalid_json():
    with pytest.raises(ProviderError, match="not valid JSON"):
        HttpProvider("http://sut", client=client(lambda r: httpx.Response(200, text="<html>"))).ask("q")


def test_redirects_are_not_followed_so_keys_stay_on_one_host():
    provider = HttpProvider("http://sut", {"Authorization": "Bearer secret"}, retries=0)
    assert provider._poster._client.follow_redirects is False


def chat_reply(content, usage=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": usage or {}})


def test_openai_request_and_usage():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return chat_reply("SELECT 1", {"prompt_tokens": 5, "completion_tokens": 2})

    provider = OpenAICompatibleProvider("http://llm/v1/", "m", "be careful", api_key="secret", client=client(handler))
    answer = provider.ask("how many?")
    assert answer == Answer(sql="SELECT 1", usage=Usage(5, 2))
    assert seen["url"] == "http://llm/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["model"] == "m"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "be careful"},
        {"role": "user", "content": "how many?"},
    ]


def test_api_key_is_not_shown_in_errors():
    handler = lambda r: httpx.Response(401, text="bad key secret-value given")
    with pytest.raises(ProviderError) as e:
        OpenAICompatibleProvider("http://llm", "m", "p", api_key="secret-value", client=client(handler)).ask("q")
    assert "secret-value" not in str(e.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
        httpx.Response(200, json=[1]),
    ],
)
def test_openai_malformed_replies(response):
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider("http://llm", "m", "p", client=client(lambda r: response)).ask("q")


@pytest.mark.parametrize(
    "content,expected",
    [
        ("SELECT 1", {"sql": "SELECT 1"}),
        ("  select 1;  ", {"sql": "select 1;"}),
        ("WITH a AS (SELECT 1) SELECT * FROM a", {"sql": "WITH a AS (SELECT 1) SELECT * FROM a"}),
        ("Here you go:\n```sql\nSELECT 1\n```\nHope it helps", {"sql": "SELECT 1"}),
        ("```\nSELECT 1\n```", {"sql": "SELECT 1"}),
        ("```SQL\nSELECT 1\n```", {"sql": "SELECT 1"}),
        ('{"sql": "SELECT 1"}', {"sql": "SELECT 1"}),
        ('{"refused": true}', {"refused": True}),
        ("I cannot share customer emails.", {"refused": True}),
        ("", {"refused": True}),
        ("Selection of data is not possible.", {"refused": True}),
        ("DROP TABLE orders", {"sql": "DROP TABLE orders"}),
    ],
)
def test_extracting_sql_from_a_reply(content, expected):
    assert extract_answer(content) == expected


# Record and replay
class Fake:
    def __init__(self):
        self.calls = 0

    def ask(self, question, attempt=0):
        self.calls += 1
        return Answer(sql=f"SELECT '{question}', {attempt}", usage=Usage(1, 2))


def test_replay_returns_exactly_what_was_recorded(tmp_path):
    path = tmp_path / "recordings" / "r.jsonl"
    inner = Fake()
    recorder = RecordingProvider(inner, RecordingStore(path), "model-a", "v1")
    first = recorder.ask("q1")
    second = recorder.ask("q1", attempt=1)
    refused_store = RecordingStore(path)
    replay = ReplayProvider(refused_store, "model-a", "v1")
    assert replay.ask("q1") == first
    assert replay.ask("q1", attempt=1) == second
    assert inner.calls == 2


def test_refusals_replay_as_refusals(tmp_path):
    class Refuser:
        def ask(self, question, attempt=0):
            return Answer(refused=True)

    path = tmp_path / "r.jsonl"
    RecordingProvider(Refuser(), RecordingStore(path), "m", "v1").ask("q")
    assert ReplayProvider(RecordingStore(path), "m", "v1").ask("q").refused


@pytest.mark.parametrize(
    "provider_id,version,question,attempt",
    [("m", "v2", "q", 0), ("other", "v1", "q", 0), ("m", "v1", "other", 0), ("m", "v1", "q", 1)],
)
def test_any_change_of_identity_misses_the_recording(tmp_path, provider_id, version, question, attempt):
    path = tmp_path / "r.jsonl"
    RecordingProvider(Fake(), RecordingStore(path), "m", "v1").ask("q")
    with pytest.raises(ProviderError, match="no recording"):
        ReplayProvider(RecordingStore(path), provider_id, version).ask(question, attempt)


def test_key_is_stable_across_runs():
    assert recording_key("q", "m", "v1", 0) == recording_key("q", "m", "v1", 0)
    assert recording_key("ab", "c", "v", 0) != recording_key("a", "bc", "v", 0)


def test_recording_holds_no_credentials(tmp_path):
    handler = lambda r: chat_reply("SELECT 1")
    inner = OpenAICompatibleProvider("http://llm", "m", "the private prompt", api_key="secret-value", client=client(handler))
    path = tmp_path / "r.jsonl"
    RecordingProvider(inner, RecordingStore(path), "m", "v1").ask("q")
    text = path.read_text()
    assert "secret-value" not in text
    assert "the private prompt" not in text


def test_file_is_sorted_and_stable(tmp_path):
    path_a, path_b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    for path, order in ((path_a, ["q1", "q2", "q3"]), (path_b, ["q3", "q1", "q2"])):
        recorder = RecordingProvider(Fake(), RecordingStore(path), "m", "v1")
        for question in order:
            recorder.ask(question)
    assert path_a.read_text() == path_b.read_text()
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_call_records_nothing(tmp_path):
    class Broken:
        def ask(self, question, attempt=0):
            raise ProviderError("down")

    path = tmp_path / "r.jsonl"
    with pytest.raises(ProviderError):
        RecordingProvider(Broken(), RecordingStore(path), "m", "v1").ask("q")
    assert not path.exists()


@pytest.mark.parametrize("content", ["not json\n", '{"no_key": 1}\n', "[1]\n"])
def test_corrupt_recording_file_stops_loading(tmp_path, content):
    path = tmp_path / "r.jsonl"
    path.write_text(content)
    with pytest.raises(RecordingError):
        RecordingStore(path)


def test_tampered_recording_is_still_validated(tmp_path):
    path = tmp_path / "r.jsonl"
    key = recording_key("q", "m", "v1", 0)
    path.write_text(json.dumps({"key": key, "answer": {"refused": True, "sql": "SELECT 1"}}) + "\n")
    with pytest.raises(ProviderError):
        ReplayProvider(RecordingStore(path), "m", "v1").ask("q")


def test_replay_needs_no_network(tmp_path, monkeypatch):
    path = tmp_path / "r.jsonl"
    RecordingProvider(Fake(), RecordingStore(path), "m", "v1").ask("q")

    def forbidden(*args, **kwargs):
        raise AssertionError("replay touched the network")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    assert ReplayProvider(RecordingStore(path), "m", "v1").ask("q").sql
