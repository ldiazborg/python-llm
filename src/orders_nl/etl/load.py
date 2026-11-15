"""ETL: raw CSV -> clean -> PostgreSQL table -> views.

Usage:
    uv run orders-load --csv data/orders_raw.csv

`run_pipeline` is the reusable entry point (used by the CLI and the
integration tests); `load_orders` only writes a clean DataFrame.

Steps of `run_pipeline`:
    1. read the CSV as text (read_raw_csv)
    2. clean it (etl/clean.py) and keep the report
    3. create the table if needed (sql/schema.sql)
    4. replace its contents (load_orders)
    5. (re)create the views (sql/views.sql)

Runs as the admin/owner user (it creates and writes tables); the API never
uses these credentials.
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from orders_nl.config import DB_SCHEMA, get_settings
from orders_nl.db import admin_engine, apply_sql_file
from orders_nl.domain import COLUMNS
from orders_nl.etl.clean import DEFAULT_STEPS, CleaningReport, CleaningStep, clean_orders

# Name of the clean table inside DB_SCHEMA (must match sql/schema.sql).
ORDERS_TABLE = "orders"


@dataclass(frozen=True)
class LoadResult:
    """Outcome of a pipeline run: what cleaning did and how many rows were stored."""

    report: CleaningReport
    rows_loaded: int


def read_raw_csv(path: Path) -> pd.DataFrame:
    """Read the raw CSV keeping every value as text.

    keep_default_na=False keeps blanks as "" instead of NaN, so the cleaner sees
    exactly what is in the file (e.g. "N/A" stays a string to be rejected).
    """
    # dtype=str: no type guessing by pandas (it would turn "20250315" into an int).
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")


def load_orders(df: pd.DataFrame, engine: Engine, schema: str = DB_SCHEMA) -> int:
    """Replace the contents of `<schema>.orders` with `df`; return rows written.

    TRUNCATE + append inside one transaction: idempotent, all-or-nothing, and it
    keeps the table created by schema.sql (constraints, indexes, grants), which
    `to_sql(if_exists="replace")` would drop. `schema` and the table name are
    code constants, never user input, so interpolating them is safe.

    Args:
        df: clean orders, as returned by `clean_orders`.
        engine: admin engine.
        schema: target schema (the table name is ORDERS_TABLE).
    """
    # engine.begin(): if the insert fails (e.g. a CHECK violation), the TRUNCATE
    # is rolled back too and the previous data stays in place.
    with engine.begin() as conn:
        conn.exec_driver_sql(f"TRUNCATE TABLE {schema}.{ORDERS_TABLE}")
        # .loc[:, COLUMNS]: insert columns in the table's order, nothing extra.
        df.loc[:, list(COLUMNS)].to_sql(
            ORDERS_TABLE,
            conn,
            schema=schema,
            if_exists="append",
            index=False,  # don't write the pandas index as a column
            method="multi",  # multi-row INSERTs: far fewer round trips
            chunksize=500,  # rows per INSERT statement
        )
    return len(df)


def run_pipeline(
    csv_path: Path,
    engine: Engine,
    steps: Sequence[CleaningStep] = DEFAULT_STEPS,
) -> LoadResult:
    """Read, clean, create/refresh the table, load it and (re)build the views.

    Args:
        csv_path: raw CSV (e.g. the output of orders-generate).
        engine: admin engine.
        steps: cleaning steps, in case a caller wants a custom pipeline.
    """
    clean, report = clean_orders(read_raw_csv(csv_path), steps=steps)
    apply_sql_file(engine, "schema.sql")  # idempotent: CREATE ... IF NOT EXISTS
    rows = load_orders(clean, engine)
    apply_sql_file(engine, "views.sql")  # after the load: views depend on the table
    return LoadResult(report=report, rows_loaded=rows)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point (`orders-load`, declared in pyproject.toml).

    Args:
        argv: arguments to parse; None means sys.argv (tests can pass a list).
    """
    parser = argparse.ArgumentParser(description="Clean the raw orders CSV and load it into PostgreSQL.")
    parser.add_argument("--csv", type=Path, default=Path("data/orders_raw.csv"), help="raw CSV path")
    args = parser.parse_args(argv)

    engine = admin_engine(get_settings().admin_sqlalchemy_url)
    try:
        result = run_pipeline(args.csv, engine)
    finally:
        # Close pooled connections even if the pipeline fails.
        engine.dispose()

    # Print every counter of the cleaning report, aligned.
    for field, value in vars(result.report).items():
        print(f"  {field:<30} {value}")
    print(f"Loaded {result.rows_loaded} rows into {DB_SCHEMA}.{ORDERS_TABLE} and rebuilt the views")


if __name__ == "__main__":
    main()
