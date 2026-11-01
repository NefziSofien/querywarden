"""Refuse to run when the database setup would make untrusted SQL unsafe."""

import ipaddress
from dataclasses import dataclass

import psycopg
from psycopg import conninfo

_DANGEROUS_ROLES = (
    "pg_read_all_data",
    "pg_write_all_data",
    "pg_read_server_files",
    "pg_write_server_files",
    "pg_execute_server_program",
    "pg_signal_backend",
)
_DANGEROUS_FUNCTIONS = (
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "pg_file_write",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_connect",
    "dblink_connect_u",
    "dblink_exec",
    "dblink_send_query",
)
_USER_SCHEMAS = "n.nspname NOT IN ('pg_catalog', 'information_schema') AND NOT starts_with(n.nspname, 'pg_toast') AND NOT starts_with(n.nspname, 'pg_temp')"


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str


class PreflightFailed(Exception):
    def __init__(self, findings: list[Finding]):
        self.findings = findings
        super().__init__("; ".join(f"{f.code}: {f.detail}" for f in findings))


def is_local_host(host: str | None) -> bool:
    """Unix sockets, loopback addresses and single-label names such as Docker services."""
    if not host or host.startswith("/"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return "." not in host or host == "localhost" or host.endswith(".localhost")


def check_target(dsn: str, allow_remote: bool = False) -> list[Finding]:
    hosts = (conninfo.conninfo_to_dict(dsn).get("host") or "").split(",")
    if allow_remote:
        return []
    return [
        Finding("remote_host", f"{h} is not local; pass allow_remote only for a disposable test database")
        for h in hosts
        if not is_local_host(h)
    ]


def check_role(conn: psycopg.Connection, forbidden: list[str]) -> list[Finding]:
    findings = [*_role_attributes(conn), *_memberships(conn), *_owned_objects(conn)]
    findings += [*_database_rights(conn), *_write_rights(conn), *_functions(conn)]
    findings += _forbidden_access(conn, forbidden)
    return findings


def run_preflight(dsn: str, forbidden: list[str], allow_remote: bool = False) -> None:
    findings = check_target(dsn, allow_remote)
    if not findings:
        with psycopg.connect(dsn, autocommit=True) as conn:
            findings = check_role(conn, forbidden)
    if findings:
        raise PreflightFailed(findings)


def _role_attributes(conn: psycopg.Connection) -> list[Finding]:
    row = conn.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls"
        " FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    names = ("superuser", "createrole", "createdb", "replication", "bypassrls")
    return [Finding("role_attribute", f"role has {n}") for n, on in zip(names, row) if on]


def _memberships(conn: psycopg.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT r FROM unnest(%s::text[]) AS r"
        " WHERE to_regrole(r) IS NOT NULL AND pg_has_role(current_user, r, 'MEMBER')",
        [list(_DANGEROUS_ROLES)],
    ).fetchall()
    return [Finding("role_membership", f"role is a member of {r}") for (r,) in rows]


def _owned_objects(conn: psycopg.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE pg_has_role(current_user, c.relowner, 'USAGE') AND " + _USER_SCHEMAS +
        " UNION ALL SELECT n.nspname FROM pg_namespace n"
        " WHERE pg_has_role(current_user, n.nspowner, 'USAGE') AND " + _USER_SCHEMAS
    ).fetchall()
    return [Finding("owns_object", f"role owns {name}") for (name,) in rows]


def _database_rights(conn: psycopg.Connection) -> list[Finding]:
    findings = []
    row = conn.execute(
        "SELECT has_database_privilege(current_database(), 'CREATE'),"
        " has_database_privilege(current_database(), 'TEMP')"
    ).fetchone()
    if row[0]:
        findings.append(Finding("can_create", "role can create schemas in this database"))
    if row[1]:
        findings.append(Finding("can_create", "role can create temporary tables"))
    rows = conn.execute(
        "SELECT n.nspname FROM pg_namespace n WHERE has_schema_privilege(n.oid, 'CREATE') AND " + _USER_SCHEMAS
    ).fetchall()
    findings += [Finding("can_create", f"role can create objects in schema {s}") for (s,) in rows]
    return findings


def _write_rights(conn: psycopg.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND " + _USER_SCHEMAS +
        " AND has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')"
        " UNION ALL SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND " + _USER_SCHEMAS +
        " AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped"
        " AND has_column_privilege(c.oid, a.attnum, 'INSERT, UPDATE, REFERENCES'))"
        " UNION ALL SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        # CASE guarantees the relkind test runs first: this function raises on anything but a sequence.
        " WHERE " + _USER_SCHEMAS + " AND CASE WHEN c.relkind = 'S' THEN has_sequence_privilege(c.oid, 'UPDATE, USAGE') ELSE false END"
    ).fetchall()
    return [Finding("can_write", f"role can write to {name}") for name in sorted({r[0] for r in rows})]


def _functions(conn: psycopg.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT p.oid::regprocedure::text, p.prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE has_function_privilege(p.oid, 'EXECUTE')"
        " AND (p.proname = ANY(%s) OR (p.prosecdef AND " + _USER_SCHEMAS + "))",
        [list(_DANGEROUS_FUNCTIONS)],
    ).fetchall()
    return [
        Finding("security_definer" if definer else "dangerous_function", f"role can execute {name}")
        for name, definer in rows
    ]


def _forbidden_access(conn: psycopg.Connection, forbidden: list[str]) -> list[Finding]:
    """Each entry is schema.table or schema.table.column. A typo must fail, never pass silently."""
    findings = []
    for entry in forbidden:
        parts = entry.split(".")
        if len(parts) not in (2, 3):
            findings.append(Finding("bad_forbidden_entry", f"{entry} must be schema.table or schema.table.column"))
            continue
        # Catalog lookup, because casting to regclass raises when the role has no schema USAGE,
        # which is the safe setup. Without USAGE the object cannot be read at all.
        table = conn.execute(
            "SELECT c.oid, has_schema_privilege(n.oid, 'USAGE') FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s",
            parts[:2],
        ).fetchone()
        if table is None:
            findings.append(Finding("bad_forbidden_entry", f"{entry} does not exist"))
            continue
        oid, schema_usable = table
        if len(parts) == 2:
            readable = conn.execute("SELECT has_any_column_privilege(%s::oid, 'SELECT')", [oid]).fetchone()[0]
        else:
            column = conn.execute(
                "SELECT has_column_privilege(a.attrelid, a.attnum, 'SELECT') FROM pg_attribute a"
                " WHERE a.attrelid = %s AND a.attname = %s AND NOT a.attisdropped",
                [oid, parts[2]],
            ).fetchone()
            if column is None:
                findings.append(Finding("bad_forbidden_entry", f"{entry} does not exist"))
                continue
            readable = column[0]
        if readable and schema_usable:
            findings.append(Finding("forbidden_readable", f"role can read {entry}"))
    return findings
