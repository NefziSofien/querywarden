"""Each unsafe setup must stop the run with a specific finding."""

import psycopg
import pytest

from querywarden.preflight import PreflightFailed, check_target, is_local_host, run_preflight

FORBIDDEN = ["public.customers.email", "public.customers"]

# name -> SQL that makes a fresh role unsafe, expected finding code
UNSAFE_SETUPS = {
    "superuser": ("ALTER ROLE {r} SUPERUSER", "role_attribute"),
    "createdb": ("ALTER ROLE {r} CREATEDB", "role_attribute"),
    "createrole": ("ALTER ROLE {r} CREATEROLE", "role_attribute"),
    "bypassrls": ("ALTER ROLE {r} BYPASSRLS", "role_attribute"),
    "read_server_files": ("GRANT pg_read_server_files TO {r}", "role_membership"),
    "execute_program": ("GRANT pg_execute_server_program TO {r}", "role_membership"),
    "read_all_data": ("GRANT pg_read_all_data TO {r}", "role_membership"),
    "signal_backend": ("GRANT pg_signal_backend TO {r}", "role_membership"),
    "table_insert": ("GRANT USAGE ON SCHEMA public TO {r}; GRANT INSERT ON public.orders TO {r}", "can_write"),
    "table_delete": ("GRANT DELETE ON public.orders TO {r}", "can_write"),
    "view_update": ("GRANT UPDATE ON reporting.orders TO {r}", "can_write"),
    "column_update": ("GRANT UPDATE (total) ON public.orders TO {r}", "can_write"),
    "sequence": ("GRANT USAGE ON SEQUENCE public.invoice_seq TO {r}", "can_write"),
    "schema_create": ("GRANT CREATE ON SCHEMA reporting TO {r}", "can_create"),
    "db_create": ("GRANT CREATE ON DATABASE test TO {r}", "can_create"),
    "db_temp": ("GRANT TEMPORARY ON DATABASE test TO {r}", "can_create"),
    "owns_table": ("CREATE TABLE reporting.owned_{r} (a int); ALTER TABLE reporting.owned_{r} OWNER TO {r}", "owns_object"),
    "forbidden_table": ("GRANT USAGE ON SCHEMA public TO {r}; GRANT SELECT ON public.customers TO {r}", "forbidden_readable"),
    "forbidden_column": ("GRANT USAGE ON SCHEMA public TO {r}; GRANT SELECT (email) ON public.customers TO {r}", "forbidden_readable"),
    "dangerous_function": ("GRANT EXECUTE ON FUNCTION pg_read_file(text) TO {r}", "dangerous_function"),
    "security_definer": (
        "CREATE FUNCTION reporting.leak_{r}() RETURNS text LANGUAGE sql SECURITY DEFINER"
        " AS 'SELECT email FROM public.customers LIMIT 1'",
        "security_definer",
    ),
    "inherited_write": ("CREATE ROLE w_{r}; GRANT DELETE ON public.orders TO w_{r}; GRANT w_{r} TO {r}", "can_write"),
}
# Grants made through a role cannot be cleaned up by dropping owned objects alone.
BASE_GRANTS = "GRANT USAGE ON SCHEMA reporting TO {r}; GRANT SELECT ON reporting.orders TO {r}"


def test_safe_role_passes(db):
    run_preflight(db.reader_dsn, FORBIDDEN)


@pytest.mark.parametrize("name", UNSAFE_SETUPS)
def test_unsafe_setup_is_refused(db, name):
    role = f"bad_{name}"
    setup, expected = UNSAFE_SETUPS[name]
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{role}'")
        conn.execute(BASE_GRANTS.format(r=role))
        conn.execute(setup.format(r=role))
        try:
            with pytest.raises(PreflightFailed) as e:
                run_preflight(db.dsn_for(role), FORBIDDEN)
            assert expected in {f.code for f in e.value.findings}, e.value
        finally:
            conn.execute(f"DROP FUNCTION IF EXISTS reporting.leak_{role}()")
            conn.execute(f"DROP OWNED BY {role}")
            conn.execute(f"DROP ROLE {role}")
            if conn.execute("SELECT to_regrole(%s)", [f"w_{role}"]).fetchone()[0]:
                conn.execute(f"DROP OWNED BY w_{role}")
                conn.execute(f"DROP ROLE w_{role}")
    run_preflight(db.reader_dsn, FORBIDDEN)


@pytest.mark.parametrize(
    "entry",
    ["public.custmers", "public.customers.emial", "customers", "a.b.c.d"],
)
def test_forbidden_list_typos_stop_the_run(db, entry):
    with pytest.raises(PreflightFailed) as e:
        run_preflight(db.reader_dsn, [entry])
    assert {f.code for f in e.value.findings} == {"bad_forbidden_entry"}


@pytest.mark.parametrize(
    "host,local",
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("postgres", True),
        ("/var/run/postgresql", True),
        (None, True),
        ("db.example.com", False),
        ("prod.internal.example.com", False),
        ("10.0.0.5", False),
        ("8.8.8.8", False),
    ],
)
def test_local_host_detection(host, local):
    assert is_local_host(host) is local


def test_remote_host_is_refused_unless_allowed():
    dsn = "host=db.example.com dbname=x"
    assert [f.code for f in check_target(dsn)] == ["remote_host"]
    assert check_target(dsn, allow_remote=True) == []
