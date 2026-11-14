"""ETL: raw CSV -> clean -> PostgreSQL table -> views.

Usage:
    uv run orders-load --csv data/orders_raw.csv

`run_pipeline` is the reusable entry point (used by the CLI and the
integration tests); `load_orders` only writes a clean DataFrame.
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
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")


def load_orders(df: pd.DataFrame, engine: Engine, schema: str = DB_SCHEMA) -> int:
    """Replace the contents of `<schema>.orders` with `df`; return rows written.

    TRUNCATE + append inside one transaction: idempotent, all-or-nothing, and it
    keeps the table created by schema.sql (constraints, indexes, grants), which
    `to_sql(if_exists="replace")` would drop. `schema` and the table name are
    code constants, never user input, so interpolating them is safe.
    """
    with engine.begin() as conn:
        conn.exec_driver_sql(f"TRUNCATE TABLE {schema}.{ORDERS_TABLE}")
        df.loc[:, list(COLUMNS)].to_sql(
            ORDERS_TABLE,
            conn,
            schema=schema,
            if_exists="append",
            index=False,
            method="multi",  # multi-row INSERTs: far fewer round trips
            chunksize=500,
        )
    return len(df)


def run_pipeline(
    csv_path: Path,
    engine: Engine,
    steps: Sequence[CleaningStep] = DEFAULT_STEPS,
) -> LoadResult:
    """Read, clean, create/refresh the table, load it and (re)build the views."""
    clean, report = clean_orders(read_raw_csv(csv_path), steps=steps)
    apply_sql_file(engine, "schema.sql")
    rows = load_orders(clean, engine)
    apply_sql_file(engine, "views.sql")  # after the load: views depend on the table
    return LoadResult(report=report, rows_loaded=rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Clean the raw orders CSV and load it into PostgreSQL.")
    parser.add_argument("--csv", type=Path, default=Path("data/orders_raw.csv"), help="raw CSV path")
    args = parser.parse_args(argv)

    engine = admin_engine(get_settings().admin_sqlalchemy_url)
    try:
        result = run_pipeline(args.csv, engine)
    finally:
        engine.dispose()

    for field, value in vars(result.report).items():
        print(f"  {field:<30} {value}")
    print(f"Loaded {result.rows_loaded} rows into {DB_SCHEMA}.{ORDERS_TABLE} and rebuilt the views")


if __name__ == "__main__":
    main()
