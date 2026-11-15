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
    3. limit      add LIMIT row_limit, or replace a bigger / unbounded one
    4. render     return the normalized SQL that will actually be executed
"""

import string
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
    # "(SELECT ...) ORDER BY x LIMIT 5" stays wrapped (see _parse_single_statement);
    # unnest() gives the query inside the parentheses.
    query = tree.unnest() if isinstance(tree, exp.Subquery) else tree
    if not isinstance(query, (exp.Select, exp.SetOperation)):
        # .key is the node type, e.g. "drop", "insert", "command".
        raise InvalidQueryError(f"Only SELECT queries are allowed (got {query.key.upper()})")


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


# PostgreSQL folds unquoted identifiers to lower case, ASCII letters only.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _identifier_name(node: exp.Expression | None) -> str:
    """The name PostgreSQL resolves an identifier to; "" if `node` isn't one.

    `ORDERS`, `Orders` and `orders` are the same relation, `"ORDERS"` is a
    different one. Names must be compared the way the database will read
    them: otherwise `WITH "PG_ROLES" AS (...) SELECT * FROM PG_ROLES` looks
    like a CTE reference here while PostgreSQL reads the catalog `pg_roles`.
    """
    if not isinstance(node, exp.Identifier):
        return ""  # e.g. a function call in FROM
    return node.name if node.quoted else node.name.translate(_ASCII_LOWER)


def _table_name(table: exp.Table) -> str:
    """Folded relation name of a table reference ("" for table functions)."""
    return _identifier_name(table.this)


def _cte_name(cte: exp.Expression) -> str:
    """Folded name a CTE is defined under (`WITH <name> AS (...)`)."""
    alias = cte.args.get("alias")
    return _identifier_name(alias.this) if alias is not None else ""


def _with_clause(node: exp.Expression) -> exp.With | None:
    """The WITH clause attached to `node`, if any."""
    # The argument was renamed "with" -> "with_" between sqlglot versions.
    for key in ("with_", "with"):
        clause = node.args.get(key)
        if isinstance(clause, exp.With):
            return clause
    return None


def _cte_names_in_scope(table: exp.Table) -> set[str]:
    """Names of the CTEs that are visible from where `table` is written.

    A CTE is not a query-wide name. PostgreSQL only sees it
      - in the query its WITH clause belongs to (and anything nested in it),
      - in the CTEs defined *after* it in the same WITH clause,
      - in its own body and in earlier siblings only under WITH RECURSIVE.
    Anywhere else the same name is a real relation, so treating every CTE
    name as global would let these read the catalog `pg_roles`:

        WITH pg_roles AS (SELECT * FROM pg_roles) SELECT * FROM pg_roles, orders
        SELECT * FROM orders, (WITH pg_roles AS (SELECT 1) SELECT * FROM pg_roles) x, pg_roles

    Walks from the table up to the root, collecting what each level exposes.
    """
    names: set[str] = set()
    own_with: exp.With | None = None  # WITH clause the table sits inside, handled below
    child: exp.Expression = table
    node = table.parent
    while node is not None:
        if isinstance(node, exp.With):
            # The table is inside the body of one of this clause's CTEs (`child`).
            siblings = list(node.expressions)
            if node.args.get("recursive"):
                visible = siblings  # recursive: itself and every sibling, any order
            else:
                # `is`: identity, not equality (two CTEs can have equal contents).
                position = next((i for i, cte in enumerate(siblings) if cte is child), 0)
                visible = siblings[:position]  # only the CTEs defined before it
            names.update(_cte_name(cte) for cte in visible)
            own_with = node  # don't add the whole clause again at its owner
        else:
            clause = _with_clause(node)
            if clause is not None and clause is not own_with:
                # A query above the table: all of its CTEs are visible down here.
                names.update(_cte_name(cte) for cte in clause.expressions)
        child, node = node, node.parent
    names.discard("")
    return names


def _is_cte_reference(table: exp.Table) -> bool:
    """True when `table` points at a CTE in scope rather than a real relation.

    Unqualified names resolve to a visible CTE first in PostgreSQL, so
    `WITH orders AS (SELECT 1) SELECT * FROM orders` does NOT read the table.
    A schema-qualified name never refers to a CTE.
    """
    return table.args.get("db") is None and _table_name(table) in _cte_names_in_scope(table)


def _allowed_list(policy: QueryPolicy) -> str:
    """Allowed tables as a sorted, comma-separated string for error messages."""
    return ", ".join(sorted(policy.allowed_tables))


def restrict_tables(tree: exp.Expression, policy: QueryPolicy) -> None:
    """Every table read must be an allowed table/view, or a CTE defined in the query.

    Checks every table reference in the tree: FROM, JOIN, subqueries, CTE bodies
    and both sides of a UNION. Blocks system catalogs (pg_catalog.*,
    information_schema.*), other schemas and other databases.
    """
    for table in tree.find_all(exp.Table):
        # this = table, db = schema, catalog = database (in "db.schema.table").
        name = _table_name(table)
        if not name:
            # FROM some_function(...) / ROWS FROM (...): not a plain table.
            raise InvalidQueryError("Table functions are not allowed")
        schema = table.args.get("db")
        # `is not None`, not truthiness: a qualifier that is present must match,
        # whatever it folds to.
        if table.args.get("catalog") is not None or (
            schema is not None and _identifier_name(schema) != policy.schema
        ):
            raise InvalidQueryError(f"Access to {table.sql(dialect='postgres')} is not allowed")
        if _is_cte_reference(table):
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
    # any(): stop at the first real, allowed table found.
    reads_data = any(
        _table_name(table) in policy.allowed_tables and not _is_cte_reference(table)
        for table in tree.find_all(exp.Table)
    )
    if not reads_data:
        raise InvalidQueryError(
            f"The query does not read any data table; it must use at least one of: {_allowed_list(policy)}"
        )


# Functions that read server files/settings, sleep, open connections, have
# side effects, or run SQL / read relations named in a *string* argument (which
# would sidestep restrict_tables). Prefix match on pg_/lo_ covers pg_sleep,
# pg_read_file, pg_ls_dir, lo_import, ... ; everything else (sum, extract,
# date_trunc, round, coalesce, to_char, ...) stays available to the LLM.
# A blocklist (not an allowlist) on purpose: the LLM legitimately uses many
# ordinary functions, and the read-only role is the backstop for anything missed.
#   has_   has_table_privilege, has_schema_privilege, ...: privilege probing
#   txid_  transaction-id functions (txid_current assigns an id)
_FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "file_", "has_", "txid_")
# query_to_xml, table_to_xmlschema, schema_to_xml_and_xmlschema, cursor_to_xml...:
# the whole XML-mapping family takes a query or a relation name as text.
_FORBIDDEN_FUNCTION_FRAGMENTS = ("_to_xml",)
_FORBIDDEN_FUNCTIONS = frozenset({
    # session / server state
    "set_config", "current_setting", "nextval", "setval",
    # run a query passed as text (full-text search helpers)
    "ts_stat", "ts_rewrite",
    # who/where am I: role, database, server and client addresses, running SQL
    "version", "current_user", "session_user", "current_role", "user", "getpgusername",
    "current_database", "current_catalog", "current_schema", "current_schemas", "current_query",
    "inet_server_addr", "inet_server_port", "inet_client_addr", "inet_client_port",
    # comments stored on arbitrary database objects
    "obj_description", "col_description", "shobj_description",
})
# Of those, the ones PostgreSQL also accepts as a bare word, which sqlglot then
# parses as a column rather than a function: `SELECT user FROM orders`.
_FORBIDDEN_BARE_KEYWORDS = frozenset({"user", "current_role"})


def _function_names(func: exp.Func) -> set[str]:
    """Every name `func` goes by: as parsed, and as it will be sent to PostgreSQL.

    sqlglot models known functions as typed nodes whose canonical name can
    differ from the PostgreSQL one (`version()` is exp.CurrentVersion, named
    "CURRENT_VERSION"), and anything else as exp.Anonymous carrying the written
    name ("pg_sleep"). Checking only the canonical name would let `version()`
    through, so the name in the rendered SQL is checked as well.
    """
    names = {(func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()}
    # Text before the first "(" of the rendered call: "VERSION" in "VERSION()",
    # all of "CURRENT_USER". Operators ("a || b") and CASE have no such name.
    head = func.sql(dialect="postgres").split("(", 1)[0]
    if head.isidentifier():
        names.add(head.lower())
    return names


def _is_forbidden_function(name: str) -> bool:
    """True if `name` (lower case) is blocked by name, prefix or fragment."""
    return (
        name in _FORBIDDEN_FUNCTIONS
        # str.startswith accepts a tuple: true if any prefix matches.
        or name.startswith(_FORBIDDEN_FUNCTION_PREFIXES)
        or any(fragment in name for fragment in _FORBIDDEN_FUNCTION_FRAGMENTS)
    )


def forbid_dangerous_functions(tree: exp.Expression, policy: QueryPolicy) -> None:
    """Reject calls to system/administrative functions."""
    for func in tree.find_all(exp.Func):
        # sorted(): the same name is reported on every run.
        for name in sorted(_function_names(func)):
            if _is_forbidden_function(name):
                raise InvalidQueryError(f"Function '{name}' is not allowed")
    for column in tree.find_all(exp.Column):
        # Unqualified and unquoted only: "user" in quotes or t.user is a real column.
        identifier = column.this
        if (
            isinstance(identifier, exp.Identifier)
            and not identifier.quoted
            and not column.table
            and identifier.name.lower() in _FORBIDDEN_BARE_KEYWORDS
        ):
            raise InvalidQueryError(f"Function '{identifier.name.lower()}' is not allowed")


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
        """Parse `sql` and return its only statement, without redundant parentheses."""
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
        # "(SELECT ...)" -> SELECT ...: harmless wrapping some models add. Only a
        # bare wrapper is removed: in "(SELECT ...) ORDER BY x LIMIT 5" the ORDER BY
        # and LIMIT belong to the wrapper, and dropping it would silently return
        # different rows than the query asked for.
        while isinstance(tree, exp.Subquery) and not any(
            value for key, value in tree.args.items() if key != "this"
        ):
            tree = tree.this
        return tree

    def _enforce_limit(self, tree: exp.Expression) -> exp.Expression:
        """Keep a plain LIMIT/FETCH that is a literal <= row_limit, otherwise set LIMIT row_limit."""
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

        Also infinity for `FETCH FIRST n ROWS WITH TIES` (it adds every row tied
        with the n-th, possibly the whole table) and for PERCENT: neither bounds
        the result to n rows.

        float so "no limit" can be represented as infinity and compared with <=.
        """
        if node is None:
            return float("inf")
        # The flags sit on the node itself or on its `limit_options` child,
        # depending on the sqlglot version: look in both.
        for holder in (node, node.args.get("limit_options")):
            if isinstance(holder, exp.Expression) and (holder.args.get("with_ties") or holder.args.get("percent")):
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
