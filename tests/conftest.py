import os
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import conninfo
from testcontainers.community.postgres import PostgresContainer

SCHEMA = """
CREATE TABLE public.customers (
    id int PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL
);
CREATE SEQUENCE public.invoice_seq;
CREATE TABLE public.orders (
    id int PRIMARY KEY,
    customer_id int NOT NULL REFERENCES public.customers,
    total numeric(10, 2) NOT NULL
);
INSERT INTO public.customers VALUES
    (1, 'Ada', 'canary-7f3a@example.test'),
    (2, 'Grace', 'grace@example.test');
INSERT INTO public.orders VALUES (1, 1, 10.50), (2, 1, 20.00), (3, 2, 5.25);

CREATE SCHEMA reporting;
CREATE VIEW reporting.orders AS SELECT id, customer_id, total FROM public.orders;
CREATE VIEW reporting.customer_names AS SELECT id, name FROM public.customers;
CREATE VIEW reporting.customer_contacts AS SELECT id, name, lower(email) AS contact FROM public.customers;
CREATE VIEW reporting.contacts_again AS SELECT id, contact AS reach_at FROM reporting.customer_contacts;
CREATE VIEW reporting.people AS
    WITH c AS (SELECT id, email AS mail FROM public.customers) SELECT id, mail FROM c
    UNION ALL SELECT id, 'none' FROM public.orders;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE, TEMPORARY ON DATABASE test FROM PUBLIC;
CREATE ROLE reader LOGIN PASSWORD 'reader';
GRANT USAGE ON SCHEMA reporting TO reader;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO reader;
"""
FORBIDDEN = ["public.customers.email", "public.customers"]


@dataclass(frozen=True)
class Database:
    admin_dsn: str
    reader_dsn: str

    def dsn_for(self, role: str) -> str:
        return conninfo.make_conninfo(self.admin_dsn, user=role, password=role)

    def snapshot(self):
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            return conn.execute(
                "SELECT (SELECT md5(string_agg(c::text, ',' ORDER BY id)) FROM public.customers c),"
                " (SELECT md5(string_agg(o::text, ',' ORDER BY id)) FROM public.orders o),"
                " (SELECT last_value FROM public.invoice_seq),"
                " (SELECT count(*) FROM pg_class),"
                " (SELECT count(*) FROM pg_largeobject_metadata)"
            ).fetchone()


@pytest.fixture(scope="session")
def db():
    image = os.environ.get("SQLGATE_PG_IMAGE", "postgres:17-alpine")
    with PostgresContainer(image, username="admin", password="admin", dbname="test", driver=None) as pg:
        host, port = pg.get_container_host_ip(), pg.get_exposed_port(5432)
        admin = conninfo.make_conninfo(host=host, port=port, dbname="test", user="admin", password="admin")
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(SCHEMA)
        yield Database(admin, conninfo.make_conninfo(admin, user="reader", password="reader"))
