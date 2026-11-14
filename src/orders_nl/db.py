"""Database access: admin engine for the ETL, read-only query runner for the API."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import Engine, create_engine

from orders_nl.errors import InvalidQueryError, QueryExecutionError, QueryTimeoutError

SQL_DIR = Path(__file__).parent / "sql"


@dataclass(frozen=True)
class QueryResult:
    """Rows of a query plus its column names (known even when there are no rows)."""

    columns: list[str]
    rows: list[dict[str, Any]]


# (sql) -> QueryResult. The service depends only on this signature, so tests and
# other backends can provide their own runner.
QueryRunner = Callable[[str], QueryResult]

# PostgreSQL SQLSTATE classes that mean "the query itself is wrong" (bad column,
# syntax, permission, bad cast...), i.e. the LLM produced invalid SQL.
# https://www.postgresql.org/docs/current/errcodes-appendix.html
_QUERY_FAULT_CLASSES = frozenset({"42", "22"})
_QUERY_CANCELED = "57014"  # raised by statement_timeout


def admin_engine(url: str) -> Engine:
    """SQLAlchemy engine for the owner/ETL user (pandas.to_sql needs SQLAlchemy)."""
    return create_engine(url)


def apply_sql_file(engine: Engine, name: str) -> None:
    """Execute a multi-statement script from `orders_nl/sql/` in one transaction.

    Uses the raw psycopg cursor rather than SQLAlchemy's `text()`: `text()` would
    read `::date` casts as bind parameters, and psycopg only runs several
    statements in one call when no parameters are passed.
    """
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    with engine.begin() as conn:
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
    """

    def run(sql: str) -> QueryResult:
        try:
            with psycopg.connect(conninfo, row_factory=dict_row) as conn:
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute(sql)  # type: ignore[arg-type]  # validated upstream
                    columns = [col.name for col in cur.description or []]
                    rows = [{k: _to_json_friendly(v) for k, v in row.items()} for row in cur.fetchall()]
        except psycopg.Error as exc:
            raise _translate_db_error(exc) from exc
        return QueryResult(columns=columns, rows=rows)

    return run


def _translate_db_error(exc: psycopg.Error) -> Exception:
    """Map a psycopg error to InvalidQueryError / QueryTimeoutError / QueryExecutionError."""
    sqlstate = exc.sqlstate or ""
    if sqlstate == _QUERY_CANCELED:
        return QueryTimeoutError("The query took too long and was cancelled")
    if sqlstate[:2] in _QUERY_FAULT_CLASSES:
        # Postgres' primary message ("column \"x\" does not exist") is safe and
        # useful to show; details/hints/internal positions are left out.
        return InvalidQueryError(f"The database rejected the query: {exc.diag.message_primary}")
    return QueryExecutionError("The query could not be executed")
