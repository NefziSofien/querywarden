# GitHub Action

The action runs `querywarden run`, writes the report to the job summary, and fails the job when a gate fails. It can also keep one PR comment up to date.

To see it block a pull request, look at [this example](https://github.com/NefziSofien/querywarden-example/pull/1). To run the tool without the action, install it with `pip install querywarden-cli`.

```yaml
jobs:
  querywarden:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write # only for comment-on-pr
    env:
      QUERYWARDEN_DATABASE_URL: ${{ secrets.QUERYWARDEN_DATABASE_URL }}
    steps:
      - uses: actions/checkout@v4
      - run: docker compose up -d --wait # your test database
      - uses: NefziSofien/querywarden@v0.2.0
        with:
          comment-on-pr: "true"
```

The database must be running before this step, with a role limited as described in `DESIGN.md`. The tool refuses a remote host unless `allow_remote` is set in the config.

| Input | Default | Meaning |
|---|---|---|
| `config` | `querywarden/querywarden.yaml` | Path to the config |
| `baseline` | none | A previous JSON report. Without one, accuracy is not gated |
| `allowed-drop` | `0` | Accuracy drop to tolerate, from 0 to 1 |
| `repeats` | `1` | Ask each question this many times |
| `strict` | `false` | Also fail on newly failing cases, removed cases and unknown lineage |
| `comment-on-pr` | `false` | Post the report as a PR comment |
| `github-token` | the job's token | Token used to post the comment |

| Output | Meaning |
|---|---|
| `result` | `pass`, `fail` or `setup-error` |
| `report-json` | Path to the JSON report, usable as the next baseline. Empty after a setup error |
| `report-markdown` | Path to the Markdown report. Empty after a setup error |

Pull requests from forks get a read-only token, so the comment fails. The run then warns, and the gate's result does not change.
