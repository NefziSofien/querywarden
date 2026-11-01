import pytest

from querywarden.config import ConfigError, load_cases, load_config, load_policy


def write(tmp_path, text):
    path = tmp_path / "file.yaml"
    path.write_text(text)
    return path


def test_valid_cases_and_defaults(tmp_path):
    path = write(
        tmp_path,
        """
defaults: {ordered: true}
cases:
  - {id: a, question: q1, gold: SELECT 1}
  - {id: b, question: q2, expect: refusal, ordered: false}
""",
    )
    a, b = load_cases(path)
    assert a.options.ordered
    assert not b.options.ordered
    assert b.expect == "refusal"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "cases: []",
        "cases: nope",
        "[1, 2]",
        "cases: [{id: a, question: q}]",
        "cases: [{id: a, question: q, gold: SELECT 1, expect: refusal}]",
        "cases: [{id: a, gold: SELECT 1}]",
        "cases: [{id: a, question: q, gold: SELECT 1, typo_field: 1}]",
        "cases: [{id: a, question: q, expect: maybe}]",
        "cases: [{id: a, question: q, gold: SELECT 1, float_tolerance: -1}]",
        "cases: [{id: a, question: q, gold: SELECT 1}, {id: a, question: r, gold: SELECT 2}]",
        "cases: [{id: a, question: q, gold: ",
    ],
)
def test_bad_case_files_are_refused(tmp_path, text):
    with pytest.raises(ConfigError):
        load_cases(write(tmp_path, text))


def test_policy_defaults_to_empty(tmp_path):
    assert load_policy(write(tmp_path, "")).forbidden == ()


@pytest.mark.parametrize("text", ["forbiden: [a.b]", "canaries: ['---']", "forbidden: 1"])
def test_bad_policy_is_refused(tmp_path, text):
    with pytest.raises(ConfigError):
        load_policy(write(tmp_path, text))


@pytest.mark.parametrize(
    "text",
    ["provider: {type: replay, id: x}", "provider: {type: magic, id: x, prompt_version: v}", "password: secret\nprovider: {type: replay, id: x, prompt_version: v}"],
)
def test_bad_config_is_refused(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))
