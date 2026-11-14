"""Database access: admin engine for the ETL, read-only runner for the API."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import Engine, create_engine

SQL_DIR = Path(__file__).parent / "sql"

# (sql) -> list of row dicts. The API depends on this signature so tests can fake it.
QueryRunner = Callable[[str], list[dict[str, Any]]]


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


def make_readonly_runner(conninfo: str) -> QueryRunner:
    """Run already-validated SQL on a read-only connection.

    Defense in depth: the role itself is read-only with a statement timeout
    (docker/postgres/init/01-roles.sh); the session is forced read-only here too.
    """

    def run(sql: str) -> list[dict[str, Any]]:
        with psycopg.connect(conninfo, row_factory=dict_row) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(sql)  # type: ignore[arg-type]  # validated upstream
                return cur.fetchall()

    return run
