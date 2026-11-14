"""Integration tests against the real PostgreSQL from docker compose.

Opt-in (they TRUNCATE and reload analytics.orders):
    docker compose up -d postgres
    RUN_INTEGRATION=1 uv run pytest -m integration
"""

import os
from pathlib import Path

import pytest

from orders_nl.config import get_settings
from orders_nl.db import admin_engine, make_readonly_runner
from orders_nl.errors import AppError
from orders_nl.etl.load import run_pipeline
from orders_nl.generation import GeneratorConfig, generate_orders

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1 to run"),
]


@pytest.fixture(scope="module")
def loaded(tmp_path_factory: pytest.TempPathFactory):
    """Generate a dataset, run the full ETL once and share the result."""
    csv_path: Path = tmp_path_factory.mktemp("data") / "orders_raw.csv"
    generate_orders(GeneratorConfig(rows=1000, seed=42)).to_csv(csv_path, index=False, encoding="utf-8")
    engine = admin_engine(get_settings().admin_sqlalchemy_url)
    try:
        yield run_pipeline(csv_path, engine)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def reader():
    return make_readonly_runner(get_settings().reader_conninfo)


def test_pipeline_loads_all_clean_rows(loaded, reader) -> None:
    rows = reader("SELECT count(*) AS n FROM orders").rows
    assert rows[0]["n"] == loaded.rows_loaded == loaded.report.rows_out


def test_pipeline_is_idempotent(loaded, tmp_path: Path) -> None:
    """Re-running replaces the data instead of appending (no PK violation)."""
    csv_path = tmp_path / "again.csv"
    generate_orders(GeneratorConfig(rows=1000, seed=42)).to_csv(csv_path, index=False, encoding="utf-8")
    engine = admin_engine(get_settings().admin_sqlalchemy_url)
    try:
        assert run_pipeline(csv_path, engine).rows_loaded == loaded.rows_loaded
    finally:
        engine.dispose()


def test_views_are_readable_by_the_api_role(loaded, reader) -> None:
    top = reader("SELECT * FROM v_top_customers").rows
    assert [r["customer_rank"] for r in top] == list(range(1, 11))
    assert top[0]["total_amount"] >= top[-1]["total_amount"]

    monthly = reader("SELECT sum(total_amount) AS t FROM v_sales_by_month_country").rows
    completed = reader("SELECT sum(amount) AS t FROM orders WHERE status = 'completed'").rows
    assert monthly[0]["t"] == completed[0]["t"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "CREATE TABLE analytics.hack (x int)",
        "SELECT * FROM pg_shadow",
    ],
)
def test_api_role_cannot_write_or_read_secrets(loaded, reader, sql: str) -> None:
    """Database-level safety net, independent of the SQL validator.

    The runner translates psycopg errors into the app's error hierarchy.
    """
    with pytest.raises(AppError):
        reader(sql)


def test_runner_reports_columns_and_json_friendly_values(loaded, reader) -> None:
    result = reader("SELECT country, sum(amount) AS total FROM orders GROUP BY country LIMIT 0")
    assert result.columns == ["country", "total"]  # known even with no rows
    total = reader("SELECT sum(amount) AS total FROM orders").rows[0]["total"]
    assert isinstance(total, float)


def test_database_rejection_is_an_invalid_query(loaded, reader) -> None:
    from orders_nl.errors import InvalidQueryError

    with pytest.raises(InvalidQueryError, match="does not exist"):
        reader("SELECT no_such_column FROM orders")


def test_statement_timeout_is_reported(loaded, reader) -> None:
    from orders_nl.errors import QueryTimeoutError

    with pytest.raises(QueryTimeoutError):
        reader("SELECT pg_sleep(6)")  # role has statement_timeout = 5s
