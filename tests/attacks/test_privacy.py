"""Leaks through approved views: the role may read them, so only the privacy checks can catch these."""

from dataclasses import replace

import psycopg
import pytest

from querywarden.canary import find_canaries
from querywarden.executor import Executor, Status
from querywarden.lineage import check_lineage, load_catalog
from querywarden.preflight import run_preflight

EMAIL = "public.customers.email"
FORBIDDEN = [EMAIL]
CANARY = "canary-7f3a@example.test"

LINEAGE_LEAKS = {
    "direct": "SELECT email FROM public.customers",
    "star": "SELECT * FROM public.customers",
    "alias": "SELECT c.email AS contact FROM public.customers AS c",
    "table alias star": "SELECT c.* FROM public.customers c",
    "cte": "WITH t AS (SELECT email AS e FROM public.customers) SELECT e FROM t",
    "nested cte": "WITH a AS (SELECT email FROM public.customers), b AS (SELECT email AS x FROM a) SELECT x FROM b",
    "subquery": "SELECT e FROM (SELECT email AS e FROM public.customers) s",
    "scalar subquery": "SELECT (SELECT max(email) FROM public.customers) AS x",
    "concatenation": "SELECT name || ' ' || email FROM public.customers",
    "function": "SELECT upper(email) FROM public.customers",
    "aggregate": "SELECT string_agg(email, ',') FROM public.customers",
    "case": "SELECT CASE WHEN id = 1 THEN email END FROM public.customers",
    "where oracle": "SELECT id FROM public.customers WHERE email LIKE 'a%'",
    "order by oracle": "SELECT id FROM public.customers ORDER BY email",
    "group by": "SELECT count(*) FROM public.customers GROUP BY email",
    "join condition": "SELECT o.id FROM reporting.orders o JOIN public.customers c ON c.email = 'x' AND c.id = o.customer_id",
    "union": "SELECT name FROM public.customers UNION ALL SELECT email FROM public.customers",
    "exists": "SELECT id FROM reporting.orders o WHERE EXISTS (SELECT 1 FROM public.customers c WHERE c.email = 'x')",
    "correlated": "SELECT o.id FROM reporting.orders o WHERE o.id = (SELECT c.id FROM public.customers c WHERE c.email = 'x' AND c.id = o.customer_id)",
    "lateral": "SELECT l.e FROM reporting.orders o, LATERAL (SELECT c.email AS e FROM public.customers c WHERE c.id = o.customer_id) l",
    "whole row": "SELECT c FROM public.customers c",
    "whole row to json": "SELECT to_jsonb(c) FROM public.customers c",
    "whole row cast": "SELECT c::text FROM public.customers c",
    "through view": "SELECT contact FROM reporting.customer_contacts",
    "view star": "SELECT * FROM reporting.customer_contacts",
    "view in where": "SELECT name FROM reporting.customer_contacts WHERE contact LIKE 'a%'",
    "view of a view": "SELECT reach_at FROM reporting.contacts_again",
    "view whole row": "SELECT to_jsonb(v) FROM reporting.customer_contacts v",
    "view with cte and union": "SELECT mail FROM reporting.people",
    "cte over view": "WITH t AS (SELECT contact FROM reporting.customer_contacts) SELECT contact FROM t",
    "quoted identifiers": 'SELECT "email" FROM "public"."customers"',
    "uppercase": "SELECT EMAIL FROM PUBLIC.CUSTOMERS",
}
LINEAGE_CLEAN = {
    "other table": "SELECT id, total FROM reporting.orders",
    "other column of same table": "SELECT name FROM public.customers",
    "safe column of a leaking view": "SELECT name FROM reporting.customer_contacts",
    "safe column of a view of a view": "SELECT id FROM reporting.contacts_again",
    "safe view": "SELECT * FROM reporting.customer_names",
    "count star": "SELECT count(*) FROM reporting.orders",
    "same name in a cte": "WITH t AS (SELECT name AS email FROM public.customers) SELECT email FROM t",
    "alias named like the secret": "SELECT name AS email FROM reporting.customer_names",
    "literal only": "SELECT 'email' AS a, 1 AS b",
    "aggregates": "SELECT customer_id, sum(total) FROM reporting.orders GROUP BY 1 HAVING sum(total) > 1",
}
LINEAGE_UNKNOWN = [
    "SELECT query_to_xml('SELECT email FROM public.customers', true, true, '')",
    "SELECT table_to_xml('public.customers', true, true, '')",
    "SELECT nonexistent_column FROM reporting.orders",
    "SELECT * FROM (SELECT",
]
# Each one really runs on the database through a readable view.
CANARY_LEAKS = [
    "SELECT contact FROM reporting.customer_contacts",
    "SELECT upper(contact) FROM reporting.customer_contacts",
    "SELECT name || ' <' || contact || '>' FROM reporting.customer_contacts",
    "SELECT string_agg(contact, ',') FROM reporting.customer_contacts",
    "SELECT array_agg(contact) FROM reporting.customer_contacts",
    "SELECT jsonb_agg(contact) FROM reporting.customer_contacts",
    "SELECT to_jsonb(v) FROM reporting.customer_contacts v",
    "SELECT v::text FROM reporting.customer_contacts v",
    "SELECT reverse(contact) FROM reporting.customer_contacts",
    "SELECT convert_to(contact, 'UTF8') FROM reporting.customer_contacts",
    "SELECT reach_at FROM reporting.contacts_again",
]
# Documented limits: the canary misses these, so the lineage check has to catch them.
CANARY_MISSES = [
    "SELECT md5(contact) FROM reporting.customer_contacts",
    "SELECT replace(contact, '@', ' at ') FROM reporting.customer_contacts",
    "SELECT encode(convert_to(contact, 'UTF8'), 'base64') FROM reporting.customer_contacts",
    "SELECT split_part(contact, '@', 1) FROM reporting.customer_contacts",
]


@pytest.fixture(scope="module")
def catalog(db):
    with psycopg.connect(db.reader_dsn, autocommit=True) as conn:
        return load_catalog(conn)


def test_setup_passes_preflight_so_these_leaks_are_reachable(db):
    run_preflight(db.reader_dsn, FORBIDDEN)


@pytest.mark.parametrize("name", LINEAGE_LEAKS)
def test_lineage_finds_the_forbidden_column(catalog, name):
    assert check_lineage(LINEAGE_LEAKS[name], catalog, FORBIDDEN).violations == [EMAIL]


@pytest.mark.parametrize("name", LINEAGE_CLEAN)
def test_lineage_passes_clean_queries(catalog, name):
    report = check_lineage(LINEAGE_CLEAN[name], catalog, FORBIDDEN)
    assert report.violations == []
    assert report.unknown == []


@pytest.mark.parametrize("sql", LINEAGE_UNKNOWN)
def test_what_cannot_be_analyzed_is_reported(catalog, sql):
    assert check_lineage(sql, catalog, FORBIDDEN).unknown


def test_unqualified_table_follows_the_search_path(catalog):
    on_path = replace(catalog, search_path=("reporting",))
    assert check_lineage("SELECT contact FROM customer_contacts", on_path, FORBIDDEN).violations == [EMAIL]
    assert check_lineage("SELECT email FROM customers", replace(catalog, search_path=("public",)), FORBIDDEN).violations == [EMAIL]


def test_forbidden_table_covers_every_use(catalog):
    for sql in ["SELECT name FROM public.customers", "SELECT count(*) FROM public.customers", "SELECT name FROM reporting.customer_names"]:
        assert check_lineage(sql, catalog, ["public.customers"]).violations == ["public.customers"]
    assert check_lineage("SELECT id FROM reporting.orders", catalog, ["public.customers"]).violations == []


@pytest.mark.parametrize("sql", CANARY_LEAKS)
def test_canary_catches_real_leaks(db, catalog, sql):
    result = Executor(db.reader_dsn).execute(sql)
    assert result.status is Status.OK, result
    assert find_canaries(result, [CANARY]) == [CANARY]
    assert check_lineage(sql, catalog, FORBIDDEN).violations == [EMAIL]


@pytest.mark.parametrize("sql", CANARY_MISSES)
def test_lineage_covers_what_the_canary_misses(db, catalog, sql):
    result = Executor(db.reader_dsn).execute(sql)
    assert result.status is Status.OK, result
    assert find_canaries(result, [CANARY]) == []
    assert check_lineage(sql, catalog, FORBIDDEN).violations == [EMAIL]


def test_clean_query_has_no_canary(db):
    result = Executor(db.reader_dsn).execute("SELECT name FROM reporting.customer_names")
    assert find_canaries(result, [CANARY]) == []
