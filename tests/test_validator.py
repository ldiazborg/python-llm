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
    # Unquoted identifiers fold to lower case in PostgreSQL: same relations.
    "SELECT * FROM ORDERS",
    "SELECT * FROM Analytics.V_Top_Customers",
    "WITH Big AS (SELECT * FROM orders WHERE amount > 100) SELECT COUNT(*) FROM big",
    # CTE scoping: a later CTE may read an earlier one; a subquery has its own WITH.
    "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT COUNT(*) FROM b",
    "SELECT * FROM orders WHERE customer IN (WITH t AS (SELECT customer FROM v_top_customers) SELECT customer FROM t)",
    "WITH RECURSIVE n AS (SELECT 1 AS i FROM orders UNION SELECT i + 1 FROM n WHERE i < 3) SELECT i FROM n",
    "(SELECT * FROM orders) ORDER BY amount DESC LIMIT 5",  # modifiers on the parentheses
    # Ordinary functions stay available, including ones starting like blocked prefixes.
    "SELECT LOWER(customer), LOG(amount + 1), TO_CHAR(order_date, 'YYYY-MM') FROM orders",
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
    # A CTE name only hides a real relation where the CTE is visible.
    "WITH pg_roles AS (SELECT * FROM pg_roles) SELECT rolname FROM pg_roles, orders",  # not in its own body
    "WITH a AS (SELECT * FROM pg_user), pg_user AS (SELECT 1 AS x) SELECT * FROM a, orders",  # defined later
    "SELECT * FROM orders, (WITH pg_roles AS (SELECT 1 AS a) SELECT a FROM pg_roles) x, pg_roles r",  # out of scope
    'WITH "PG_ROLES" AS (SELECT 1 AS a) SELECT * FROM PG_ROLES, orders',  # quoted CTE != folded name
    'SELECT * FROM "ORDERS"',  # quoted upper case is a different relation
    # Functions that take a query or a relation name as text bypass the table check.
    "SELECT query_to_xml('select * from pg_roles', true, false, '') FROM orders",
    "SELECT query_to_xmlschema('select * from pg_roles', true, false, '') FROM orders",
    "SELECT table_to_xml_and_xmlschema('pg_roles', true, false, '') FROM orders",
    "SELECT (ts_stat('select to_tsvector(rolname) from pg_roles')).word FROM orders",
    # Server / session information.
    "SELECT version() FROM orders",
    "SELECT current_database() FROM orders",
    "SELECT current_user FROM orders",
    "SELECT user FROM orders",
    "SELECT has_table_privilege('pg_authid', 'select') FROM orders",
    'SELECT "pg_sleep"(4) FROM orders',
    "SELECT pg_catalog.pg_sleep(4) FROM orders",
]


@pytest.mark.parametrize("sql", VALID)
def test_accepts_read_only_queries_on_allowed_tables(sql: str) -> None:
    """Legitimate read-only queries pass (CTEs, UNION, subqueries...)."""
    assert validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)


@pytest.mark.parametrize("sql", INVALID)
def test_rejects_unsafe_or_unknown(sql: str) -> None:
    """Writes, system tables, other schemas, dangerous functions and junk are rejected."""
    with pytest.raises(InvalidQueryError):
        validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)


def test_error_message_names_the_problem() -> None:
    """The rejection reason names the offending table and the allowed ones."""
    with pytest.raises(InvalidQueryError, match="Table 'customers' is not allowed"):
        validate_sql("SELECT * FROM customers", ALLOWED_TABLES, row_limit=LIMIT)


def test_adds_limit_when_missing() -> None:
    """A query without LIMIT gets LIMIT row_limit."""
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
    """LIMIT above row_limit, LIMIT ALL and UNION limits are lowered to row_limit."""
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
    """A LIMIT/FETCH below row_limit is kept as the model wrote it."""
    assert expected in validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT).upper()


@pytest.mark.parametrize(
    "sql",
    [
        # WITH TIES also returns every row tied with the last one: here, the whole table.
        "SELECT * FROM orders ORDER BY status FETCH FIRST 1 ROWS WITH TIES",
        "SELECT * FROM orders LIMIT '50000'",  # not a plain integer literal
    ],
)
def test_replaces_limits_that_do_not_bound_the_rows(sql: str) -> None:
    """A FETCH/LIMIT that can return more than row_limit rows is replaced, even if its number is small."""
    out = validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT).upper()
    assert out.endswith(f"LIMIT {LIMIT}")
    assert "TIES" not in out
    assert "50000" not in out


def test_parenthesised_query_keeps_its_order_and_limit() -> None:
    """Regression: unwrapping "(SELECT ...) ORDER BY x LIMIT n" used to drop the ORDER BY and LIMIT."""
    out = validate_sql("(SELECT * FROM orders) ORDER BY amount DESC LIMIT 5", ALLOWED_TABLES, row_limit=LIMIT)
    assert out == "(SELECT * FROM orders) ORDER BY amount DESC LIMIT 5"
    # Without a limit of its own, the cap goes on the outer query.
    out = validate_sql("(SELECT * FROM orders) ORDER BY amount DESC", ALLOWED_TABLES, row_limit=LIMIT)
    assert out == f"(SELECT * FROM orders) ORDER BY amount DESC LIMIT {LIMIT}"


@pytest.mark.parametrize(
    ("sql", "table"),
    [
        ("WITH pg_roles AS (SELECT * FROM pg_roles) SELECT rolname FROM pg_roles, orders", "pg_roles"),
        ("WITH a AS (SELECT * FROM pg_user), pg_user AS (SELECT 1 AS x) SELECT * FROM a, orders", "pg_user"),
        ("SELECT * FROM orders, (WITH pg_roles AS (SELECT 1 AS a) SELECT a FROM pg_roles) x, pg_roles r", "pg_roles"),
        ('WITH "PG_ROLES" AS (SELECT 1 AS a) SELECT * FROM PG_ROLES, orders', "pg_roles"),
    ],
)
def test_cte_name_does_not_hide_a_real_table_outside_its_scope(sql: str, table: str) -> None:
    """Regression: any CTE name was treated as query-wide, so these read system catalogs."""
    with pytest.raises(InvalidQueryError, match=f"Table '{table}' is not allowed"):
        validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)


@pytest.mark.parametrize(
    ("sql", "function"),
    [
        ("SELECT version() FROM orders", "version"),  # sqlglot calls it CURRENT_VERSION
        ("SELECT query_to_xmlschema('select 1', true, false, '') FROM orders", "query_to_xmlschema"),
        ("SELECT (ts_stat('select 1')).word FROM orders", "ts_stat"),
        ("SELECT user FROM orders", "user"),  # keyword function without parentheses
    ],
)
def test_rejected_function_is_named(sql: str, function: str) -> None:
    """The reason names the function under the name PostgreSQL knows it by."""
    with pytest.raises(InvalidQueryError, match=f"Function '{function}' is not allowed"):
        validate_sql(sql, ALLOWED_TABLES, row_limit=LIMIT)


def test_custom_rule_extends_the_validator() -> None:
    """Rules are pluggable: e.g. forbid SELECT * on top of the defaults."""

    def no_star(tree: exp.Expression, policy: QueryPolicy) -> None:
        """Example extra rule: forbid SELECT *."""
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
