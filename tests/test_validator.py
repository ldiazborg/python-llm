"""SQL validation tests (requirement 4): read-only, allowed tables only."""

import pytest

from conftest import todo
from orders_nl.config import ALLOWED_TABLES
from orders_nl.validator import InvalidQueryError, validate_sql

LIMIT = 1000

VALID = [
    "SELECT * FROM orders",
    "SELECT country, SUM(amount) FROM orders GROUP BY country",
    "SELECT * FROM v_sales_by_month_country WHERE country = 'España'",
    "SELECT * FROM analytics.v_top_customers",
    "WITH m AS (SELECT * FROM orders WHERE status = 'completed') SELECT COUNT(*) FROM m",
    "SELECT customer FROM orders UNION SELECT customer FROM v_top_customers",
]

INVALID = [
    "",
    "this is not sql",
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET amount = 0",
    "INSERT INTO orders (order_id) VALUES ('x')",
    "SELECT 1; DROP TABLE orders",
    "SELECT * FROM customers",  # not in allowlist
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM public.orders",  # wrong schema
    "SELECT * FROM orders o JOIN pg_shadow s ON true",
    "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "COPY orders TO '/tmp/out.csv'",
    "SET statement_timeout = 0",
]


@todo
@pytest.mark.parametrize("sql", VALID)
def test_accepts_read_only_queries_on_allowed_tables(sql: str) -> None:
    assert validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)


@todo
@pytest.mark.parametrize("sql", INVALID)
def test_rejects_unsafe_or_unknown(sql: str) -> None:
    with pytest.raises(InvalidQueryError):
        validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)


@todo
def test_adds_limit_when_missing() -> None:
    out = validate_sql("SELECT * FROM orders", ALLOWED_TABLES, row_limit=LIMIT)
    assert f"LIMIT {LIMIT}" in out.upper()


@todo
def test_clamps_excessive_limit() -> None:
    out = validate_sql("SELECT * FROM orders LIMIT 50000", ALLOWED_TABLES, row_limit=LIMIT)
    assert "50000" not in out
    assert f"LIMIT {LIMIT}" in out.upper()
