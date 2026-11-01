"""Find which forbidden columns a query uses, following CTEs, subqueries and views.

Any use counts, not only returned columns: a WHERE clause on a secret can still reveal it.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

import psycopg
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage as column_lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

_DIALECT = "postgres"
# These run SQL hidden in a string, which static analysis cannot see.
_DYNAMIC_SQL_FUNCTIONS = {
    "query_to_xml",
    "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    "table_to_xml",
    "table_to_xmlschema",
    "table_to_xml_and_xmlschema",
    "schema_to_xml",
    "database_to_xml",
    "cursor_to_xml",
    "ts_stat",
    "ts_rewrite",
}

ColumnRef = tuple[str, str, str]


@dataclass(frozen=True)
class Catalog:
    columns: dict[str, dict[str, dict[str, str]]]  # schema -> table -> column -> type
    views: dict[tuple[str, str], str]
    search_path: tuple[str, ...]


@dataclass(frozen=True)
class LineageReport:
    violations: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def load_catalog(conn: psycopg.Connection) -> Catalog:
    """Catalogs are readable by any role, so this also sees columns the role cannot select."""
    columns: dict[str, dict[str, dict[str, str]]] = {}
    for schema, table, column, type_name in conn.execute(
        "SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod)"
        " FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
        " AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND NOT starts_with(n.nspname, 'pg_toast')"
        " ORDER BY n.nspname, c.relname, a.attnum"
    ):
        columns.setdefault(schema, {}).setdefault(table, {})[column] = type_name
    views = {
        (schema, name): definition
        for schema, name, definition in conn.execute(
            "SELECT n.nspname, c.relname, pg_get_viewdef(c.oid) FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('v', 'm')"
            " AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
        )
    }
    search_path = tuple(conn.execute("SELECT current_schemas(false)").fetchone()[0])
    return Catalog(columns, views, search_path)


def check_lineage(sql: str, catalog: Catalog, forbidden: Iterable[str]) -> LineageReport:
    """Cannot analyze something? Report it as unknown, because silence would read as safe."""
    analysis = _Analysis(catalog, list(forbidden))
    try:
        analysis.visit_query(sql)
    except (SqlglotError, ValueError, KeyError, AttributeError) as e:
        analysis.unknown.add(f"could not analyze query: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
    return LineageReport(sorted(analysis.violations), sorted(analysis.unknown))


class _Analysis:
    def __init__(self, catalog: Catalog, forbidden: list[str]):
        self.catalog = catalog
        self.forbidden_tables = {tuple(f.split(".")) for f in forbidden if f.count(".") == 1}
        self.forbidden_columns = {tuple(f.split(".")) for f in forbidden if f.count(".") == 2}
        self.violations: set[str] = set()
        self.unknown: set[str] = set()
        self._expanded: set[ColumnRef] = set()

    def visit_query(self, sql: str) -> None:
        tree = self._prepare(sql)
        self._note_dynamic_sql(tree)
        for scope in traverse_scope(tree):
            for table in scope.tables:
                self._use_table(table.db, table.name)
            for column in scope.columns:
                self._visit_column(scope, column)
            for row in scope.find_all(exp.TableColumn):
                self._visit_whole_row(scope, row.name)
            for star in scope.expression.find_all(exp.Star):
                if not isinstance(star.parent, (exp.Count, exp.Column)):
                    self.unknown.add("query has a star that could not be expanded")

    def _prepare(self, sql: str | exp.Expression) -> exp.Expression:
        tree = sqlglot.parse_one(sql, dialect=_DIALECT) if isinstance(sql, str) else sql
        self._resolve_tables(tree)
        return qualify(
            tree,
            dialect=_DIALECT,
            schema=self.catalog.columns,
            validate_qualify_columns=False,
            quote_identifiers=False,
        )

    def _resolve_tables(self, tree: exp.Expression) -> None:
        """Fill in the schema using the search path, the way PostgreSQL would."""
        cte_names = {cte.alias for cte in tree.find_all(exp.CTE)}
        for table in tree.find_all(exp.Table):
            if table.db or not table.name or table.name in cte_names:
                continue
            for schema in self.catalog.search_path:
                if table.name in self.catalog.columns.get(schema, {}):
                    table.set("db", exp.to_identifier(schema))
                    break

    def _note_dynamic_sql(self, tree: exp.Expression) -> None:
        for function in tree.find_all(exp.Anonymous, exp.Func):
            name = (function.name if isinstance(function, exp.Anonymous) else function.sql_name()).lower()
            if name in _DYNAMIC_SQL_FUNCTIONS:
                self.unknown.add(f"{name} runs SQL that cannot be analyzed")

    def _visit_column(self, scope, column: exp.Column) -> None:
        source = scope.sources.get(column.table) if column.table else None
        if isinstance(source, exp.Table):
            self._use_column(source.db, source.name, column.name)
        elif not column.table:
            self.unknown.add(f"could not resolve column {column.name}")

    def _visit_whole_row(self, scope, alias: str) -> None:
        """SELECT c, to_jsonb(c) or c::text, with c a table alias, uses every column."""
        source = scope.sources.get(alias)
        if isinstance(source, exp.Table):
            for name in self.catalog.columns.get(source.db, {}).get(source.name, {}):
                self._use_column(source.db, source.name, name)

    def _use_table(self, schema: str, table: str) -> None:
        if (schema, table) in self.forbidden_tables:
            self.violations.add(f"{schema}.{table}")

    def _use_column(self, schema: str, table: str, column: str) -> None:
        reference = (schema, table, column)
        if reference in self._expanded:
            return
        self._expanded.add(reference)
        self._use_table(schema, table)
        if reference in self.forbidden_columns:
            self.violations.add(".".join(reference))
        if (schema, table) in self.catalog.views:
            self._expand_view(schema, table, column)

    def _expand_view(self, schema: str, view: str, column: str) -> None:
        tree = self._prepare(self.catalog.views[(schema, view)])
        self._note_dynamic_sql(tree)
        root = column_lineage(column, tree, schema=self.catalog.columns, dialect=_DIALECT)
        for node in root.walk():
            if isinstance(node.source, exp.Table):
                self._use_column(node.source.db, node.source.name, node.name.rsplit(".", 1)[-1])
