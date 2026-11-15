"""Query runner helpers that need no database: value conversion, column names, error mapping.

The runner itself (connection, read-only session, timeout) is covered by the
integration tests against the real PostgreSQL.
"""

import logging
from decimal import Decimal

import psycopg
import pytest

from orders_nl.config import Settings
from orders_nl.db import _to_json_friendly, _translate_db_error, _unique_names
from orders_nl.errors import InvalidQueryError, QueryExecutionError, QueryTimeoutError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("6215.32"), 6215.32),  # amount: numeric with decimals -> float
        (Decimal("6215.00"), 6215.0),  # still an amount, even when it is a round figure
        (Decimal("862"), 862),  # SUM over a bigint column: numeric without decimals -> int
        (Decimal("2025"), 2025),  # EXTRACT(YEAR FROM ...)
        (Decimal("1E+3"), 1000),
        (Decimal("NaN"), "NaN"),  # no JSON representation
        (float("inf"), "inf"),
        (35, 35),
        (1.5, 1.5),
        ("España", "España"),
        (None, None),
    ],
)
def test_values_become_json_friendly(value, expected) -> None:
    """numeric keeps its meaning (count vs amount) and nothing unserializable gets through."""
    converted = _to_json_friendly(value)
    assert converted == expected
    # bool/int/float compare equal across types (862 == 862.0): pin the type too.
    assert type(converted) is type(expected)


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["country", "total"], ["country", "total"]),
        (["count", "count"], ["count", "count_2"]),  # COUNT(*), COUNT(DISTINCT customer)
        (["?column?", "?column?", "?column?"], ["?column?", "?column?_2", "?column?_3"]),
        (["count", "count", "count_2"], ["count", "count_3", "count_2"]),  # never onto a real name
        ([], []),
    ],
)
def test_column_names_are_made_unique(names: list[str], expected: list[str]) -> None:
    """Regression: two columns with the same name collapsed into one dict key, losing a value."""
    unique = _unique_names(names)
    assert unique == expected
    assert len(set(unique)) == len(names)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (psycopg.errors.UndefinedColumn("x"), InvalidQueryError),  # 42703
        (psycopg.errors.DivisionByZero("x"), InvalidQueryError),  # 22012
        (psycopg.errors.CardinalityViolation("x"), InvalidQueryError),  # 21000: scalar subquery, many rows
        (psycopg.errors.FeatureNotSupported("x"), InvalidQueryError),  # 0A000
        (psycopg.errors.QueryCanceled("x"), QueryTimeoutError),  # 57014: statement_timeout
        (psycopg.errors.ReadOnlySqlTransaction("x"), QueryExecutionError),  # 25006
        (psycopg.OperationalError("connection refused"), QueryExecutionError),  # no SQLSTATE
    ],
)
def test_database_errors_are_translated(error: psycopg.Error, expected: type[Exception]) -> None:
    """A query the LLM got wrong is a 422, a timeout a 504, anything operational a 500."""
    assert type(_translate_db_error(error)) is expected


def test_operational_errors_are_logged_but_not_shown(caplog: pytest.LogCaptureFixture) -> None:
    """The client gets a generic message; the cause only survives in the server log."""
    with caplog.at_level(logging.ERROR, logger="orders_nl.db"):
        translated = _translate_db_error(psycopg.OperationalError("connection to server at 10.0.0.5 failed"))
    assert str(translated) == "The query could not be executed"
    assert "10.0.0.5" in caplog.text


def test_reader_connection_fails_fast() -> None:
    """The API's connection string carries a connect timeout (an unreachable host must not hang requests)."""
    assert "connect_timeout=5" in Settings(_env_file=None).reader_conninfo
