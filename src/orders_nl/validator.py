"""Validate LLM-generated SQL before execution (requirement 3).

The SQL is parsed into a sqlglot AST (never inspected with regexes) and checked
by a list of independent rules. Each rule is a function
`(tree, policy) -> None` that raises `InvalidQueryError` with a human-readable
reason. Extend by writing a rule and passing `rules=` to `SqlValidator`.

This is the first line of defense. The second is the database itself: the API
connects as a read-only role with a statement timeout and SELECT-only grants
(docker/postgres/init/01-roles.sh), so a validator bug can't write data.

Why an AST and not regexes: SQL can hide a write inside a CTE
(`WITH x AS (DELETE ...) SELECT ...`), put a table in a subquery, or use
comments and quoting to fool text matching. Walking the parsed tree sees every
node, wherever it is nested.

Pipeline of `SqlValidator.validate(sql)`:
    1. parse      exactly one statement, PostgreSQL dialect, max length
    2. rules      each rule inspects the whole tree (DEFAULT_RULES below)
    3. limit      add LIMIT row_limit, or lower a bigger one
    4. render     return the normalized SQL that will actually be executed
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
    """What a query is allowed to touch, and how many rows it may return.

    Attributes:
        allowed_tables: tables/views the query may read (unqualified names).
        schema: the only schema a qualified name may use ("analytics.orders").
        row_limit: maximum rows returned; enforced through LIMIT.
    """

    allowed_tables: frozenset[str]
    schema: str = DB_SCHEMA
    row_limit: int = 1000


# Signature every validation rule implements: inspect, raise if not allowed.
Rule = Callable[[exp.Expression, QueryPolicy], None]


# --- rules ---------------------------------------------------------------------


def require_query_root(tree: exp.Expression, policy: QueryPolicy) -> None:
    """The statement itself must be a SELECT or a set operation (UNION/INTERSECT/EXCEPT).

    Rejects DROP/INSERT/SET/COPY/EXPLAIN... and also free text that sqlglot
    parses as an expression (e.g. "this is not sql" becomes a NOT node).
    """
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        # tree.key is the node type, e.g. "drop", "insert", "command".
        raise InvalidQueryError(f"Only SELECT queries are allowed (got {tree.key.upper()})")


def _node_types(*names: str) -> tuple[type[exp.Expression], ...]:
    """Resolve sqlglot node classes by name, skipping ones this version lacks.

    sqlglot renames/adds node classes between versions (e.g. AlterTable ->
    Alter); resolving by name keeps the validator working across upgrades.
    """
    # The walrus keeps the getattr result; isinstance(..., type) skips missing names (None).
    return tuple(t for n in names if isinstance(t := getattr(exp, n, None), type))


# Anything that writes, changes state or escapes the parser, anywhere in the
# tree: covers `WITH x AS (DELETE ...)`, `SELECT ... INTO new_table` and
# `SELECT ... FOR UPDATE` (row locks), which all have a SELECT at the root.
# "Command" is sqlglot's fallback for statements it can't parse (e.g. EXPLAIN).
_FORBIDDEN_NODES = _node_types(
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
    "TruncateTable", "Copy", "Command", "Into", "Lock", "Set", "Grant", "Revoke",
    "Transaction", "Commit", "Rollback", "Use", "Analyze", "LoadData", "Kill",
)


def forbid_side_effects(tree: exp.Expression, policy: QueryPolicy) -> None:
    """No data-modifying, locking or session-changing node anywhere in the query."""
    # tree.find searches the whole subtree, depth-first, and returns the first match.
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
    # table.db is the schema qualifier ("" when unqualified).
    return not table.db and table.name in cte_names


def _allowed_list(policy: QueryPolicy) -> str:
    """Allowed tables as a sorted, comma-separated string for error messages."""
    return ", ".join(sorted(policy.allowed_tables))


def restrict_tables(tree: exp.Expression, policy: QueryPolicy) -> None:
    """Every table read must be an allowed table/view, or a CTE defined in the query.

    Checks every table reference in the tree: FROM, JOIN, subqueries, CTE bodies
    and both sides of a UNION. Blocks system catalogs (pg_catalog.*,
    information_schema.*), other schemas and other databases.
    """
    cte_names = _cte_names(tree)
    for table in tree.find_all(exp.Table):
        # name = table, db = schema, catalog = database (in "db.schema.table").
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
    # any(): stop at the first real, allowed table found.
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
# A blocklist (not an allowlist) on purpose: the LLM legitimately uses many
# ordinary functions, and the read-only role is the backstop for anything missed.
_FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "file_")
_FORBIDDEN_FUNCTIONS = frozenset({
    "set_config", "current_setting", "version", "nextval", "setval",
    "query_to_xml", "query_to_xml_and_xmlschema", "table_to_xml", "cursor_to_xml",
    "schema_to_xml", "database_to_xml", "inet_server_addr", "inet_server_port",
    "txid_current", "current_user", "session_user",
})


def _function_name(func: exp.Func) -> str:
    """Name as written for unknown functions, canonical SQL name for known ones.

    sqlglot models known functions as typed nodes (exp.Sum -> "SUM") and
    anything else as exp.Anonymous carrying the written name ("pg_sleep").
    """
    return (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()


def forbid_dangerous_functions(tree: exp.Expression, policy: QueryPolicy) -> None:
    """Reject calls to system/administrative functions."""
    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        # str.startswith accepts a tuple: true if any prefix matches.
        if name in _FORBIDDEN_FUNCTIONS or name.startswith(_FORBIDDEN_FUNCTION_PREFIXES):
            raise InvalidQueryError(f"Function '{name}' is not allowed")


# Applied in this order; the first failing rule's message is returned.
DEFAULT_RULES: tuple[Rule, ...] = (
    require_query_root,
    forbid_side_effects,
    restrict_tables,
    require_data_source,
    forbid_dangerous_functions,
)


# --- validator -------------------------------------------------------------------


class SqlValidator:
    """Parse, check and normalize one SQL query against a `QueryPolicy`.

    Example:
        validator = SqlValidator(QueryPolicy(allowed_tables=ALLOWED_TABLES))
        validator.validate("SELECT * FROM orders")  # -> "SELECT * FROM orders LIMIT 1000"
        validator.validate("DROP TABLE orders")     # -> InvalidQueryError
    """

    def __init__(self, policy: QueryPolicy, rules: Sequence[Rule] = DEFAULT_RULES) -> None:
        """Create a validator.

        Args:
            policy: what queries may access.
            rules: checks to run; pass `(*DEFAULT_RULES, my_rule)` to extend.
        """
        self.policy = policy
        self.rules = tuple(rules)  # immutable copy: later changes to the caller's list don't leak in

    def validate(self, sql: str) -> str:
        """Return SQL that is safe to run, or raise InvalidQueryError explaining why.

        The returned SQL is re-rendered from the AST (normalized formatting,
        LIMIT applied), and is exactly what should be executed.
        """
        tree = self._parse_single_statement(sql)
        for rule in self.rules:
            rule(tree, self.policy)
        return self._enforce_limit(tree).sql(dialect="postgres")

    @staticmethod
    def _parse_single_statement(sql: str) -> exp.Expression:
        """Parse `sql` and return its only statement, unwrapped from parentheses."""
        text = (sql or "").strip()
        if not text:
            raise InvalidQueryError("Empty query")
        if len(text) > MAX_SQL_LENGTH:
            raise InvalidQueryError(f"Query is longer than {MAX_SQL_LENGTH} characters")
        try:
            # parse() returns one entry per ';'-separated statement; a trailing ';'
            # yields a None entry, which is dropped here.
            statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
        except SqlglotError as exc:
            # sqlglot's own message embeds token reprs and ANSI colors; keep only
            # the position so API clients get a clean, stable message.
            raise InvalidQueryError(f"Query could not be parsed ({_syntax_error_position(exc)})") from exc
        if len(statements) != 1:
            # Blocks stacked statements such as "SELECT 1; DROP TABLE orders".
            raise InvalidQueryError(f"Exactly one statement is allowed (got {len(statements)})")

        tree = statements[0]
        # "(SELECT ...)" -> SELECT ...: harmless wrapping some models add.
        while isinstance(tree, exp.Subquery):
            tree = tree.this
        return tree

    def _enforce_limit(self, tree: exp.Expression) -> exp.Expression:
        """Keep a LIMIT/FETCH that is a literal <= row_limit, otherwise set LIMIT row_limit."""
        current = tree.args.get("limit")  # exp.Limit, exp.Fetch or None
        if self._literal_row_count(current) <= self.policy.row_limit:
            return tree  # already small enough: respect the model's own limit
        # Copy before mutating so the caller's tree is never modified.
        tree = tree.copy()
        tree.set("limit", exp.Limit(expression=exp.Literal.number(self.policy.row_limit)))
        return tree

    @staticmethod
    def _literal_row_count(node: exp.Expression | None) -> float:
        """Row count of LIMIT n / FETCH FIRST n; infinity if absent, ALL or not a literal.

        float so "no limit" can be represented as infinity and compared with <=.
        """
        if node is None:
            return float("inf")
        # FETCH FIRST n ROWS keeps n in "count"; LIMIT n keeps it in "expression".
        value = node.args.get("count") if isinstance(node, exp.Fetch) else node.expression
        if isinstance(value, exp.Literal) and value.is_int:
            return int(value.this)
        return float("inf")  # LIMIT ALL, LIMIT (SELECT ...), parameters...


def _syntax_error_position(exc: SqlglotError) -> str:
    """'syntax error near line L, column C' from a ParseError, or a generic text."""
    # ParseError.errors is a list of dicts with description/line/col; other
    # SqlglotError subclasses (e.g. TokenError) don't have it.
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
    """Convenience wrapper: validate `sql` with the default rules.

    Args:
        sql: the SQL to check (typically produced by the LLM).
        allowed_tables: tables/views the query may read.
        row_limit: maximum rows the query may return.
        schema: schema allowed in qualified names.

    Returns:
        The normalized SQL to execute.

    Raises:
        InvalidQueryError: with the reason, if the query is not acceptable.
    """
    policy = QueryPolicy(allowed_tables=frozenset(allowed_tables), schema=schema, row_limit=row_limit)
    return SqlValidator(policy).validate(sql)
