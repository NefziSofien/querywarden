import pytest

from querywarden.canary import BadCanary, find_canaries
from querywarden.executor import ExecutionResult, Status

CANARY = "canary-7f3a@example.test"


def rows(*values):
    return ExecutionResult(Status.OK, ["a"], [(v,) for v in values])


@pytest.mark.parametrize(
    "value",
    [
        CANARY,
        CANARY.upper(),
        f"Ada <{CANARY}>",
        "canary 7f3a at example test".replace(" at ", " "),
        CANARY[::-1],
        CANARY.encode(),
        memoryview(CANARY.encode()),
        [CANARY],
        {"contact": CANARY},
        {CANARY: 1},
        [{"nested": [CANARY]}],
    ],
)
def test_leaks_are_found(value):
    assert find_canaries(rows("safe", value), [CANARY]) == [CANARY]


@pytest.mark.parametrize("value", ["grace@example.test", "canary", "7f3a", None, 7, True, 1.5, b"\xff", [], {}])
def test_other_values_are_clean(value):
    assert find_canaries(rows(value), [CANARY]) == []


def test_no_canaries_configured():
    assert find_canaries(rows(CANARY), []) == []


def test_each_canary_is_reported_once():
    found = find_canaries(rows(CANARY, CANARY, "second-marker-99"), [CANARY, "second-marker-99", "absent-marker-11"])
    assert found == sorted([CANARY, "second-marker-99"])


@pytest.mark.parametrize("bad", ["", "---", "@ ."])
def test_canary_without_content_is_refused(bad):
    with pytest.raises(BadCanary):
        find_canaries(rows("x"), [bad])
