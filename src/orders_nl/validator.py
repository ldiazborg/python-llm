"""Validate LLM-generated SQL before execution (requirement 3).

The SQL is parsed into a sqlglot AST (never inspected with regexes) and checked
by a list of independent rules. Each rule is a function
`(tree, policy) -> None` that raises `InvalidQueryError` with a human-readable
reason. Extend by writing a rule and passing `rules=` to `SqlValidator`.

This is the first line of defense. The second is the database itself: the API
connects as a read-only role with a statement timeout and SELECT-only grants
(docker/postgres/init/01-roles.sh), so a validator bug can't write data.
"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from orders_nl.config import DB_SCHEMA
from orders_nl.errors import InvalidQueryError  # re-exported for convenience

__all__ = ["DEFAULT_RULES", "InvalidQueryError", "QueryPolicy", "Rule", "SqlValidator", "validate_sql"]

MAX_SQL_LENGTH = 5000  # generated queries are short; long input is suspicious


@dataclass(frozen=True)
class QueryPolicy:
    """What a query is allowed to touch, and how many rows it may return."""

    allowed_tables: frozenset[str]
    schema: str = DB_SCHEMA
    row_limit: int = 1000


Rule = Callable[[exp.Expression, QueryPolicy], None]


# --- rules ---------------------------------------------------------------------


def require_query_root(tree: exp.Expression, policy: QueryPolicy) -> None:
    """The statement itself must be a SELECT or a set operation (UNION/INTERSECT/EXCEPT)."""
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise InvalidQueryError(f"Only SELECT queries are allowed (got {tree.key.upper()})")


def _node_types(*names: str) -> tuple[type[exp.Expression], ...]:
    """Resolve sqlglot node classes by name, skipping ones this version lacks."""
    return tuple(t for n in names if isinstance(t := getattr(exp, n, None), type))


# Anything that writes, changes state or escapes the parser, anywhere in the
# tree: covers `WITH x AS (DELETE ...)`, `SELECT ... INTO new_table` and
# `SELECT ... FOR UPDATE` (row locks), which all have a SELECT at the root.
_FORBIDDEN_NODES = _node_types(
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
    "TruncateTable", "Copy", "Command", "Into", "Lock", "Set", "Grant", "Revoke",
    "Transaction", "Commit", "Rollback", "Use", "Analyze", "LoadData", "Kill",
)


def forbid_side_effects(tree: exp.Expression, policy: QueryPolicy) -> None:
    """No data-modifying, locking or session-changing node anywhere in the query."""
    node = tree.find(*_FORBIDDEN_NODES)
    if node is not None:
        raise InvalidQueryError(f"Forbidden operation in query: {node.key.upper()}")


def _cte_names(tree: exp.Expression) -> set[str]:
    """Names of the CTEs (WITH name AS ...) defined anywhere in the query."""
    return {cte.alias_or_name for cte in tree.find_all(exp.CTE)}


def _is_cte_reference(table: exp.Table, cte_names: set[str]) -> bool:
    """True when `table` points at a CTE of this query rather than a real relation.

    Unqualified names resolve to a CTE first in PostgreSQL, so
    `WITH orders AS (SELECT 1) SELECT * FROM orders` does NOT read the table.
    """
    return not table.db and table.name in cte_names


def _allowed_list(policy: QueryPolicy) -> str:
    return ", ".join(sorted(policy.allowed_tables))


def restrict_tables(tree: exp.Expression, policy: QueryPolicy) -> None:
    """Every table read must be an allowed table/view, or a CTE defined in the query."""
    cte_names = _cte_names(tree)
    for table in tree.find_all(exp.Table):
        name, schema = table.name, table.db
        if not name:
            # FROM some_function(...) / ROWS FROM (...): not a plain table.
            raise InvalidQueryError("Table functions are not allowed")
        if table.catalog or (schema and schema != policy.schema):
            raise InvalidQueryError(f"Access to {table.sql(dialect='postgres')} is not allowed")
        if _is_cte_reference(table, cte_names):
            continue  # its body is checked as part of the tree
        if name not in policy.allowed_tables:
            raise InvalidQueryError(f"Table '{name}' is not allowed. Allowed: {_allowed_list(policy)}")


def require_data_source(tree: exp.Expression, policy: QueryPolicy) -> None:
    """The query must read at least one allowed table or view.

    Catches off-topic answers from the LLM that are valid but unrelated SQL,
    e.g. "¿Qué tiempo hace en Madrid?" -> `SELECT CURRENT_TIMESTAMP`, or
    `SELECT 1`. Such queries are harmless but would return a misleading
    "answer" to a question the data can't answer. CTEs only count through the
    real tables their bodies read.
    """
    cte_names = _cte_names(tree)
    reads_data = any(
        table.name in policy.allowed_tables and not _is_cte_reference(table, cte_names)
        for table in tree.find_all(exp.Table)
    )
    if not reads_data:
        raise InvalidQueryError(
            f"The query does not read any data table; it must use at least one of: {_allowed_list(policy)}"
        )


# Functions that read server files/settings, sleep, open connections or have
# side effects. Prefix match on pg_/lo_ covers pg_sleep, pg_read_file,
# pg_ls_dir, lo_import, ... ; everything else (sum, extract, date_trunc,
# round, coalesce, to_char, ...) stays available to the LLM.
_FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "file_")
_FORBIDDEN_FUNCTIONS = frozenset({
    "set_config", "current_setting", "version", "nextval", "setval",
    "query_to_xml", "query_to_xml_and_xmlschema", "table_to_xml", "cursor_to_xml",
    "schema_to_xml", "database_to_xml", "inet_server_addr", "inet_server_port",
    "txid_current", "current_user", "session_user",
})


def _function_name(func: exp.Func) -> str:
    """Name as written for unknown functions, canonical SQL name for known ones."""
    return (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()


def forbid_dangerous_functions(tree: exp.Expression, policy: QueryPolicy) -> None:
    """Reject calls to system/administrative functions."""
    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if name in _FORBIDDEN_FUNCTIONS or name.startswith(_FORBIDDEN_FUNCTION_PREFIXES):
            raise InvalidQueryError(f"Function '{name}' is not allowed")


DEFAULT_RULES: tuple[Rule, ...] = (
    require_query_root,
    forbid_side_effects,
    restrict_tables,
    require_data_source,
    forbid_dangerous_functions,
)


# --- validator -------------------------------------------------------------------


class SqlValidator:
    """Parse, check and normalize one SQL query against a `QueryPolicy`."""

    def __init__(self, policy: QueryPolicy, rules: Sequence[Rule] = DEFAULT_RULES) -> None:
        self.policy = policy
        self.rules = tuple(rules)

    def validate(self, sql: str) -> str:
        """Return SQL that is safe to run, or raise InvalidQueryError explaining why."""
        tree = self._parse_single_statement(sql)
        for rule in self.rules:
            rule(tree, self.policy)
        return self._enforce_limit(tree).sql(dialect="postgres")

    @staticmethod
    def _parse_single_statement(sql: str) -> exp.Expression:
        text = (sql or "").strip()
        if not text:
            raise InvalidQueryError("Empty query")
        if len(text) > MAX_SQL_LENGTH:
            raise InvalidQueryError(f"Query is longer than {MAX_SQL_LENGTH} characters")
        try:
            statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
        except SqlglotError as exc:
            # sqlglot's own message embeds token reprs and ANSI colors; keep only
            # the position so API clients get a clean, stable message.
            raise InvalidQueryError(f"Query could not be parsed ({_syntax_error_position(exc)})") from exc
        if len(statements) != 1:
            raise InvalidQueryError(f"Exactly one statement is allowed (got {len(statements)})")

        tree = statements[0]
        # "(SELECT ...)" -> SELECT ...: harmless wrapping some models add.
        while isinstance(tree, exp.Subquery):
            tree = tree.this
        return tree

    def _enforce_limit(self, tree: exp.Expression) -> exp.Expression:
        """Keep a LIMIT/FETCH that is a literal <= row_limit, otherwise set LIMIT row_limit."""
        current = tree.args.get("limit")
        if self._literal_row_count(current) <= self.policy.row_limit:
            return tree
        tree = tree.copy()
        tree.set("limit", exp.Limit(expression=exp.Literal.number(self.policy.row_limit)))
        return tree

    @staticmethod
    def _literal_row_count(node: exp.Expression | None) -> float:
        """Row count of LIMIT n / FETCH FIRST n; infinity if absent, ALL or not a literal."""
        if node is None:
            return float("inf")
        value = node.args.get("count") if isinstance(node, exp.Fetch) else node.expression
        if isinstance(value, exp.Literal) and value.is_int:
            return int(value.this)
        return float("inf")


def _syntax_error_position(exc: SqlglotError) -> str:
    """'syntax error near line L, column C' from a ParseError, or a generic text."""
    errors = getattr(exc, "errors", None) or []
    if errors and errors[0].get("line") is not None:
        return f"syntax error near line {errors[0]['line']}, column {errors[0].get('col')}"
    return "invalid SQL syntax"


def validate_sql(
    sql: str,
    allowed_tables: Collection[str],
    *,
    row_limit: int,
    schema: str = DB_SCHEMA,
) -> str:
    """Convenience wrapper: validate `sql` with the default rules."""
    policy = QueryPolicy(allowed_tables=frozenset(allowed_tables), schema=schema, row_limit=row_limit)
    return SqlValidator(policy).validate(sql)
