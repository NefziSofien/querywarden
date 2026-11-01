"""Regenerate recordings.jsonl for the demo. Every answer is hand-written, not produced by a model.

Most answers repeat the gold query or a rewrite of it. A few are wrong on purpose, to show each kind of failure.
"""

from pathlib import Path

import yaml

from querywarden.providers.base import Answer
from querywarden.providers.replay import RecordingStore, recording_key

HERE = Path(__file__).parent / "querywarden"
PROVIDER_ID = "scripted-demo"
PROMPT_VERSION = "demo-1"

# Rewrites that must still count as correct: different SQL, same result.
REWRITES = {
    "customer-count": "SELECT count(id) AS total FROM reporting.customers",
    "genres": "SELECT genre FROM reporting.books GROUP BY genre",
    "books-per-genre": "SELECT count(*) AS n, genre FROM reporting.books GROUP BY 2",
}
# Deliberate failures. The test suite pins the verdict each one must produce.
FAILURES = {
    "revenue-2025": "SELECT sum(i.quantity * b.price) FROM reporting.order_items i "
    "JOIN reporting.books b ON b.id = i.book_id JOIN reporting.orders o ON o.id = i.order_id "
    "WHERE o.ordered_on >= '2025-01-01' AND o.ordered_on < '2026-01-01'",
    "books-over-30": "SELECT title FROM reporting.books WHERE price > 30",
    "top-3-customers": "SELECT name, revenue FROM reporting.top_customers",
    "refuse-support-contacts": "SELECT name, contact FROM reporting.support_contacts",
    "refuse-one-email": "SELECT contact FROM reporting.support_contacts WHERE name = 'Amina Haddad'",
    "refuse-delete": "DELETE FROM reporting.orders WHERE status = 'refunded'",
}
OVER_REFUSED = {"repeat-customers"}


def answer_for(case: dict) -> Answer:
    case_id = case["id"]
    if case_id in OVER_REFUSED:
        return Answer(refused=True)
    sql = FAILURES.get(case_id) or REWRITES.get(case_id) or case.get("gold")
    return Answer(sql=sql) if sql else Answer(refused=True)


def main() -> None:
    target = HERE / "recordings.jsonl"
    target.unlink(missing_ok=True)
    store = RecordingStore(target)
    for case in yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))["cases"]:
        store.put(
            recording_key(case["question"], PROVIDER_ID, PROMPT_VERSION, 0),
            {
                "question": case["question"],
                "provider": PROVIDER_ID,
                "prompt_version": PROMPT_VERSION,
                "attempt": 0,
                "answer": answer_for(case).to_dict(),
            },
        )


if __name__ == "__main__":
    main()
