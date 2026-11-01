#!/usr/bin/env bash
# Runs the gate, publishes the report, then exits with the gate's own code.
# A reporting problem only warns: it must never turn a failed gate into a pass, or the reverse.
set -uo pipefail

MARKER="<!-- querywarden-report -->"
SUMMARY_LIMIT=1000000
COMMENT_LIMIT=60000

reports=$(mktemp -d "${RUNNER_TEMP:-/tmp}/querywarden.XXXXXX")
json="$reports/report.json"
markdown="$reports/report.md"

args=(run --config "$QW_CONFIG" --repeats "$QW_REPEATS" --allowed-drop "$QW_ALLOWED_DROP"
  --json-out "$json" --markdown-out "$markdown")
[[ -n "$QW_BASELINE" ]] && args+=(--baseline "$QW_BASELINE")
[[ "$QW_STRICT" == "true" ]] && args+=(--strict)

"$QW_BIN" "${args[@]}"
code=$?

case "$code" in
  0) result=pass ;;
  1) result=fail ;;
  *) result=setup-error ;;
esac

# Cuts at a byte limit, then drops any character the cut left incomplete.
truncated() {
  head -c "$1" "$2" | iconv -f utf-8 -t utf-8 -c 2>/dev/null
  if [[ $(wc -c <"$2") -gt $1 ]]; then
    printf '\n\nThe report was cut short. The job log lists every failing case.\n'
  fi
}

# A failed run may leave no report, and a stale pass must not stay on the PR.
if [[ ! -s "$markdown" || "$result" == "setup-error" ]]; then
  json=""
  markdown="$reports/setup-error.md"
  printf '# querywarden: SETUP ERROR\n\nThe gate could not run. The job log has the reason.\n' >"$markdown"
fi

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  truncated "$SUMMARY_LIMIT" "$markdown" >>"$GITHUB_STEP_SUMMARY"
fi

post_comment() {
  local number repo body existing
  number=$(jq -r '.pull_request.number // empty' "${GITHUB_EVENT_PATH:-/dev/null}" 2>/dev/null)
  if [[ -z "$number" ]]; then
    echo "::notice::comment-on-pr is on, but this run is not for a pull request."
    return 0
  fi
  repo="$GITHUB_REPOSITORY"
  body="$reports/comment.md"
  { echo "$MARKER"; truncated "$COMMENT_LIMIT" "$markdown"; } >"$body"
  existing=$(gh api --paginate "repos/$repo/issues/$number/comments" \
    --jq ".[] | select(.body | startswith(\"$MARKER\")) | .id" | head -n 1) || return 1
  if [[ -n "$existing" ]]; then
    gh api --method PATCH "repos/$repo/issues/comments/$existing" -F "body=@$body" >/dev/null
  else
    gh api --method POST "repos/$repo/issues/$number/comments" -F "body=@$body" >/dev/null
  fi
}

if [[ "$QW_COMMENT" == "true" ]] && ! post_comment; then
  echo "::warning::Could not post the PR comment. A token from a fork has no write access."
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "result=$result"
    echo "report-json=$json"
    [[ "$result" == "setup-error" ]] && echo "report-markdown=" || echo "report-markdown=$markdown"
  } >>"$GITHUB_OUTPUT"
fi

exit "$code"
