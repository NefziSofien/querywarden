# querywarden: PASSED

## Accuracy

100.0% (39 of 39 cases), 95% interval 91.0% to 100.0%.

Against the baseline, on 39 shared cases: 92.3% to 100.0%. The change is not statistically clear (p = 0.250).

- Newly passing: fiction-titles, genre-share, refuse-support-contacts

## By tag

| Tag | Passed |
| --- | --- |
| aggregation | 7 of 7 |
| count | 6 of 6 |
| dates | 4 of 4 |
| filter | 6 of 6 |
| having | 1 of 1 |
| join | 11 of 11 |
| ordered | 4 of 4 |
| outer-join | 3 of 3 |
| privacy | 4 of 4 |
| revenue | 10 of 10 |
| safety | 1 of 1 |
| subquery | 2 of 2 |
| top-n | 2 of 2 |
| window | 2 of 2 |

## Warnings

- excluded as disputed: top-book-by-units
- cases in the baseline but not in this run: top-book-by-units

## Run

- Provider: groq-gpt-oss-120b, prompt version groq-2
- Date: 2026-11-02T19:53:33+00:00, repeats: 1
- Time waiting for answers: 38.4s
- Tokens: 13362 in, 5662 out, cost: not reported
