"""SQL validation tests (requirement 4): read-only, allowed tables only, bounded rows."""

import pytest
from sqlglot import exp

from orders_nl.config import ALLOWED_TABLES
from orders_nl.validator import (
    DEFAULT_RULES,
    InvalidQueryError,
    QueryPolicy,
    SqlValidator,
    validate_sql,
)

LIMIT = 1000

VALID = [
    "SELECT * FROM orders",
    "SELECT country, SUM(amount) FROM orders GROUP BY country",
    "SELECT * FROM v_sales_by_month_country WHERE country = 'España' AND month = 3",
    "SELECT * FROM analytics.v_top_customers",
    "WITH m AS (SELECT * FROM orders WHERE status = 'completed') SELECT COUNT(*) FROM m",
    "SELECT customer FROM orders UNION SELECT customer FROM v_top_customers",
    "SELECT EXTRACT(MONTH FROM order_date) AS m, ROUND(AVG(amount), 2) FROM orders GROUP BY 1",
    "SELECT * FROM (SELECT country FROM orders) AS sub",
    "(SELECT * FROM orders)",
    "SELECT * FROM orders;",  # trailing semicolon is not a second statement
    "WITH m AS (SELECT * FROM orders) SELECT COUNT(*) FROM m",  # data read via a CTE
]

INVALID = [
    "",
    "   ",
    "this is not sql",
    "SELECT * FROM",
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET amount = 0",
    "INSERT INTO orders (order_id) VALUES ('x')",
    "TRUNCATE orders",
    "SELECT 1; DROP TABLE orders",
    "SELECT * FROM customers",  # not in allowlist
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM public.orders",  # wrong schema
    "SELECT * FROM otherdb.analytics.orders",  # cross-catalog
    "SELECT * FROM orders o JOIN pg_shadow s ON true",
    "SELECT * FROM orders WHERE customer IN (SELECT usename FROM pg_user)",
    "WITH orders AS (SELECT * FROM pg_shadow) SELECT * FROM orders",  # CTE shadowing
    "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
    "SELECT * INTO stolen FROM orders",
    "SELECT * FROM orders FOR UPDATE",
    "SELECT * FROM generate_series(1, 10)",
    "SELECT * FROM LATERAL pg_ls_dir('.') AS f",
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT current_setting('data_directory')",
    "SELECT set_config('statement_timeout', '0', false)",
    "COPY orders TO '/tmp/out.csv'",
    "SET statement_timeout = 0",
    "EXPLAIN ANALYZE SELECT * FROM orders",
    "SELECT 1 " + "x" * 6000,  # too long
    # Valid SQL that reads no data table (off-topic LLM answers)
    "SELECT 1",
    "SELECT CURRENT_TIMESTAMP AS current_time",
    "SELECT 'No tengo datos del tiempo' AS respuesta",
    "WITH m AS (SELECT 1 AS x) SELECT * FROM m",
    "WITH orders AS (SELECT 1 AS x) SELECT * FROM orders",  # CTE shadows the real table
]


@pytest.mark.parametrize("sql", VALID)
def test_accepts_read_only_queries_on_allowed_tables(sql: str) -> None:
    assert validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)


@pytest.mark.parametrize("sql", INVALID)
def test_rejects_unsafe_or_unknown(sql: str) -> None:
    with pytest.raises(InvalidQueryError):
        validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)


def test_error_message_names_the_problem() -> None:
    with pytest.raises(InvalidQueryError, match="Table 'customers' is not allowed"):
        validate_sql("SELECT * FROM customers", ALLOWED_TABLES, row_limit=LIMIT)


def test_adds_limit_when_missing() -> None:
    out = validate_sql("SELECT * FROM orders", ALLOWED_TABLES, row_limit=LIMIT)
    assert f"LIMIT {LIMIT}" in out.upper()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders LIMIT 50000",
        "SELECT * FROM orders LIMIT ALL",
        "SELECT customer FROM orders UNION SELECT customer FROM orders LIMIT 50000",
    ],
)
def test_clamps_excessive_limit(sql: str) -> None:
    out = validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)
    assert "50000" not in out
    assert "ALL" not in out.upper().replace("UNION", "")
    assert f"LIMIT {LIMIT}" in out.upper()


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM orders LIMIT 5", "LIMIT 5"),
        ("SELECT * FROM orders FETCH FIRST 5 ROWS ONLY", "FETCH FIRST 5 ROWS ONLY"),
    ],
)
def test_keeps_smaller_limit(sql: str, expected: str) -> None:
    assert expected in validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT).upper()


def test_custom_rule_extends_the_validator() -> None:
    """Rules are pluggable: e.g. forbid SELECT * on top of the defaults."""

    def no_star(tree: exp.Expression, policy: QueryPolicy) -> None:
        if tree.find(exp.Star):
            raise InvalidQueryError("SELECT * is not allowed")

    validator = SqlValidator(QueryPolicy(allowed_tables=ALLOWED_TABLES), rules=(*DEFAULT_RULES, no_star))
    assert validator.validate("SELECT country FROM orders")
    with pytest.raises(InvalidQueryError, match=r"SELECT \*"):
        validator.validate("SELECT * FROM orders")


def test_parse_error_message_is_clean() -> None:
    """No sqlglot internals (token reprs, ANSI colors) leak into the reason."""
    with pytest.raises(InvalidQueryError) as info:
        validate_sql("SELECT * FROM", ALLOWED_TABLES, row_limit=LIMIT)
    assert str(info.value) == "Query could not be parsed (syntax error near line 1, column 13)"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CURRENT_TIMESTAMP AS current_time",
        "WITH orders AS (SELECT 1 AS x) SELECT * FROM orders",
    ],
)
def test_rejects_queries_that_read_no_data(sql: str) -> None:
    """Regression: the real model answered "¿Qué tiempo hace en Madrid?" with SELECT CURRENT_TIMESTAMP."""
    with pytest.raises(InvalidQueryError, match="does not read any data table"):
        validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)
