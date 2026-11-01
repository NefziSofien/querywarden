import pytest

from sqlgate.statement import Check, check_statement


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT 1;",
        "select * from reporting.orders -- DROP TABLE orders",
        "WITH t AS (SELECT 1 AS a) SELECT a FROM t",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT ';' AS semicolon, 'DROP TABLE x' AS text",
        "SELECT sum(total) OVER (PARTITION BY customer_id) FROM reporting.orders",
    ],
)
def test_allows_read_only_queries(sql):
    assert check_statement(sql).outcome is Check.ALLOWED


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders VALUES (1)",
        "UPDATE orders SET total = 0",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "TRUNCATE orders",
        "CREATE TABLE t (a int)",
        "ALTER TABLE orders ADD COLUMN a int",
        "GRANT ALL ON orders TO reader",
        "SET ROLE admin",
        "COPY orders TO '/tmp/x'",
        "DO $$ BEGIN DELETE FROM orders; END $$",
        "SELECT 1; DROP TABLE orders",
        "SELECT 1; COMMIT; DROP TABLE orders",
        "/* hi */ DELETE /* x */ FROM orders",
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
        "SELECT * INTO copy_of_orders FROM orders",
        "SELECT * FROM orders FOR UPDATE",
    ],
)
def test_blocks_everything_else(sql):
    assert check_statement(sql).outcome is Check.BLOCKED


@pytest.mark.parametrize("sql", ["", "   ", "-- only a comment"])
def test_blocks_empty_input(sql):
    assert check_statement(sql).outcome is Check.BLOCKED


def test_unparseable_sql_is_reported_separately():
    assert check_statement("SELECT FROM WHERE (").outcome is not Check.ALLOWED
