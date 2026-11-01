"""The action's script, with the tool and gh replaced by stand-ins that record their calls."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "action" / "run.sh"
MARKER = "<!-- querywarden-report -->"


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class Harness:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.summary = tmp_path / "summary.md"
        self.output = tmp_path / "output.txt"
        self.event = tmp_path / "event.json"
        self.event.write_text(json.dumps({"pull_request": {"number": 7}}), encoding="utf-8")
        (tmp_path / "bin").mkdir()
        (tmp_path / "temp").mkdir()
        self.existing_comment = ""
        self.gh_exit = 0

    def run(self, tool_exit: int, report: str = "# querywarden: PASSED\n", **overrides: str):
        tool = _executable(
            self.tmp / "tool",
            f"""printf '%s\\n' "$@" > "{self.tmp}/tool_args"
while [[ $# -gt 0 ]]; do
  if [[ $1 == --markdown-out && {tool_exit} -lt 2 ]]; then cat "{self.tmp}/report_in" > "$2"; fi
  if [[ $1 == --json-out && {tool_exit} -lt 2 ]]; then echo '{{}}' > "$2"; fi
  shift
done
exit {tool_exit}
""",
        )
        (self.tmp / "report_in").write_text(report, encoding="utf-8")
        _executable(
            self.tmp / "bin" / "gh",
            f"""printf '%s\\n' "$*" >> "{self.tmp}/gh_calls"
for arg in "$@"; do
  if [[ $arg == body=@* ]]; then cp "${{arg#body=@}}" "{self.tmp}/gh_body"; fi
done
[[ {self.gh_exit} -eq 0 && $* == *--paginate* ]] && echo "{self.existing_comment}"
exit {self.gh_exit}
""",
        )
        env = {
            **os.environ,
            "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(self.tmp / "temp"),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_EVENT_PATH": str(self.event),
            "GITHUB_REPOSITORY": "acme/shop",
            "QW_BIN": str(tool),
            "QW_CONFIG": "q.yaml",
            "QW_BASELINE": "",
            "QW_ALLOWED_DROP": "0",
            "QW_REPEATS": "1",
            "QW_STRICT": "false",
            "QW_COMMENT": "false",
            **overrides,
        }
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, check=False)

    def outputs(self) -> dict[str, str]:
        lines = self.output.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    def gh_calls(self) -> str:
        path = self.tmp / "gh_calls"
        return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


@pytest.mark.parametrize(("code", "result"), [(0, "pass"), (1, "fail"), (2, "setup-error")])
def test_keeps_the_exit_code_and_names_the_result(harness: Harness, code: int, result: str):
    assert harness.run(code).returncode == code
    assert harness.outputs()["result"] == result


def test_a_report_reaches_the_summary_and_outputs(harness: Harness):
    harness.run(1, "# querywarden: FAILED\n")
    assert "FAILED" in harness.summary.read_text(encoding="utf-8")
    assert Path(harness.outputs()["report-json"]).exists()
    assert Path(harness.outputs()["report-markdown"]).exists()


def test_a_setup_error_is_reported_without_report_paths(harness: Harness):
    harness.run(2)
    assert "SETUP ERROR" in harness.summary.read_text(encoding="utf-8")
    assert harness.outputs()["report-json"] == ""
    assert harness.outputs()["report-markdown"] == ""


def test_options_reach_the_tool_as_single_arguments(harness: Harness):
    hostile = "base line; touch pwned.json"
    harness.run(0, QW_BASELINE=hostile, QW_STRICT="true", QW_REPEATS="3", QW_ALLOWED_DROP="0.05")
    args = (harness.tmp / "tool_args").read_text(encoding="utf-8").splitlines()
    assert args[args.index("--baseline") + 1] == hostile
    assert "--strict" in args
    assert args[args.index("--repeats") + 1] == "3"
    assert args[args.index("--allowed-drop") + 1] == "0.05"


def test_no_comment_unless_asked(harness: Harness):
    harness.run(1)
    assert harness.gh_calls() == ""


def test_creates_a_comment_with_the_marker(harness: Harness):
    harness.run(1, "# querywarden: FAILED\n", QW_COMMENT="true")
    assert "--method POST repos/acme/shop/issues/7/comments" in harness.gh_calls()
    body = (harness.tmp / "gh_body").read_text(encoding="utf-8")
    assert body.startswith(MARKER)
    assert "FAILED" in body


def test_updates_its_earlier_comment_instead_of_adding_one(harness: Harness):
    harness.existing_comment = "555"
    harness.run(0, QW_COMMENT="true")
    assert "--method PATCH repos/acme/shop/issues/comments/555" in harness.gh_calls()
    assert "--method POST" not in harness.gh_calls()


def test_a_setup_error_replaces_an_earlier_comment(harness: Harness):
    harness.existing_comment = "555"
    harness.run(2, QW_COMMENT="true")
    assert "SETUP ERROR" in (harness.tmp / "gh_body").read_text(encoding="utf-8")


@pytest.mark.parametrize("code", [0, 1])
def test_a_comment_failure_warns_and_leaves_the_gate_alone(harness: Harness, code: int):
    harness.gh_exit = 1
    done = harness.run(code, QW_COMMENT="true")
    assert done.returncode == code
    assert "::warning::" in done.stdout


def test_outside_a_pull_request_no_comment_is_posted(harness: Harness):
    harness.event.write_text("{}", encoding="utf-8")
    done = harness.run(1, QW_COMMENT="true")
    assert done.returncode == 1
    assert harness.gh_calls() == ""


def test_a_long_report_is_cut_to_valid_text(harness: Harness):
    harness.run(1, "é" * 40000, QW_COMMENT="true")
    body = (harness.tmp / "gh_body").read_bytes()
    assert len(body) < 61000
    assert "cut short" in body.decode("utf-8")
