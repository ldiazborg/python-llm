"""Database access: admin engine for the ETL, read-only query runner for the API.

Two very different users of PostgreSQL, deliberately kept apart:

    ETL (orders-load)   admin/owner user, SQLAlchemy engine (pandas.to_sql needs
                        it), may create tables and write data
    API (/ask)          read-only role, plain psycopg connection per query, only
                        ever runs SQL that the validator already accepted

The API side is exposed as a `QueryRunner` (a plain callable), so the service
layer and the tests don't depend on psycopg at all.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import Engine, create_engine

from orders_nl.errors import InvalidQueryError, QueryExecutionError, QueryTimeoutError

# Folder with the packaged SQL scripts (schema.sql, views.sql). Resolved from this
# file's location, so it works no matter the current working directory.
SQL_DIR = Path(__file__).parent / "sql"


@dataclass(frozen=True)
class QueryResult:
    """Rows of a query plus its column names (known even when there are no rows).

    Attributes:
        columns: column names in SELECT order, taken from the cursor description.
        rows: one dict per row, `{column: value}`, with JSON-friendly values.
    """

    columns: list[str]
    rows: list[dict[str, Any]]


# (sql) -> QueryResult. The service depends only on this signature, so tests and
# other backends can provide their own runner.
QueryRunner = Callable[[str], QueryResult]

# PostgreSQL SQLSTATE classes that mean "the query itself is wrong" (bad column,
# syntax, permission, bad cast...), i.e. the LLM produced invalid SQL.
#   42xxx: syntax error or access rule violation (undefined column/table, ...)
#   22xxx: data exception (invalid cast, division by zero, ...)
# https://www.postgresql.org/docs/current/errcodes-appendix.html
_QUERY_FAULT_CLASSES = frozenset({"42", "22"})
_QUERY_CANCELED = "57014"  # raised by statement_timeout


def admin_engine(url: str) -> Engine:
    """SQLAlchemy engine for the owner/ETL user (pandas.to_sql needs SQLAlchemy).

    Args:
        url: SQLAlchemy URL, normally `Settings.admin_sqlalchemy_url`.
    """
    return create_engine(url)


def apply_sql_file(engine: Engine, name: str) -> None:
    """Execute a multi-statement script from `orders_nl/sql/` in one transaction.

    Uses the raw psycopg cursor rather than SQLAlchemy's `text()`: `text()` would
    read `::date` casts as bind parameters, and psycopg only runs several
    statements in one call when no parameters are passed.

    Args:
        engine: admin engine (the scripts create tables and views).
        name: file name inside `orders_nl/sql/`, e.g. "views.sql".
    """
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    # engine.begin(): commit on success, roll back everything if any statement fails.
    with engine.begin() as conn:
        # Drop down to the underlying psycopg connection of this transaction.
        dbapi_conn = conn.connection.dbapi_connection
        assert dbapi_conn is not None  # always set on an open connection
        with dbapi_conn.cursor() as cur:
            cur.execute(sql)


def _to_json_friendly(value: Any) -> Any:
    """numeric -> float so the API returns JSON numbers (pydantic would emit strings)."""
    return float(value) if isinstance(value, Decimal) else value


def make_readonly_runner(conninfo: str) -> QueryRunner:
    """Build a runner that executes already-validated SQL on a read-only connection.

    Defense in depth: the role itself is read-only with a statement timeout
    (docker/postgres/init/01-roles.sh); the session is forced read-only here too.
    psycopg errors are translated into the app's error hierarchy.

    Args:
        conninfo: libpq connection string of the read-only role
            (`Settings.reader_conninfo`).

    Returns:
        A `QueryRunner`: call it with SQL, get a `QueryResult`.
    """

    def run(sql: str) -> QueryResult:
        """Execute `sql` and return its result, or raise an `AppError` subclass.

        One short-lived connection per query: simple and safe for this load
        (see the README for connection pooling as a future improvement).
        """
        try:
            # dict_row: each row comes back as {column: value} instead of a tuple.
            with psycopg.connect(conninfo, row_factory=dict_row) as conn:
                # Must be set before the first statement; makes Postgres reject
                # any write in this session even if the role allowed it.
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute(sql)  # type: ignore[arg-type]  # validated upstream
                    # cursor.description lists the columns even for 0-row results.
                    columns = [col.name for col in cur.description or []]
                    rows = [{k: _to_json_friendly(v) for k, v in row.items()} for row in cur.fetchall()]
        except psycopg.Error as exc:
            # `from exc` keeps the original psycopg error in the traceback/logs.
            raise _translate_db_error(exc) from exc
        return QueryResult(columns=columns, rows=rows)

    return run


def _translate_db_error(exc: psycopg.Error) -> Exception:
    """Map a psycopg error to InvalidQueryError / QueryTimeoutError / QueryExecutionError.

    The mapping decides the HTTP status the client sees (see api/errors.py):
    a query the LLM got wrong is the client-visible 422, an operational
    problem is a 500, and a timeout is a 504.
    """
    sqlstate = exc.sqlstate or ""  # None for client-side errors (e.g. can't connect)
    if sqlstate == _QUERY_CANCELED:
        return QueryTimeoutError("The query took too long and was cancelled")
    if sqlstate[:2] in _QUERY_FAULT_CLASSES:
        # Postgres' primary message ("column \"x\" does not exist") is safe and
        # useful to show; details/hints/internal positions are left out.
        return InvalidQueryError(f"The database rejected the query: {exc.diag.message_primary}")
    # Anything else (connection refused, read-only violation, ...): generic
    # message so no internal detail leaks to API clients.
    return QueryExecutionError("The query could not be executed")
