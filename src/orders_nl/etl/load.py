"""Read raw CSV -> clean -> load into PostgreSQL -> (re)create views.

Usage: uv run orders-load --csv data/orders_raw.csv
"""

import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from orders_nl.config import DB_SCHEMA, get_settings
from orders_nl.db import admin_engine, apply_sql_file
from orders_nl.etl.clean import clean_orders


def load_orders(df: pd.DataFrame, engine: Engine, schema: str = DB_SCHEMA) -> int:
    """Replace the contents of `<schema>.orders` with `df`; return rows written.

    Must be idempotent (re-running the load gives the same table). Keep the table
    created by schema.sql (constraints + grants): TRUNCATE then append, rather than
    `to_sql(if_exists="replace")`, which would drop and recreate it.
    """
    raise NotImplementedError("TODO: implement load_orders")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path("data/orders_raw.csv"))
    args = parser.parse_args(argv)

    raw = pd.read_csv(args.csv, dtype=str, keep_default_na=False)
    clean, report = clean_orders(raw)
    print(report)

    engine = admin_engine(get_settings().admin_sqlalchemy_url)
    apply_sql_file(engine, "schema.sql")
    written = load_orders(clean, engine)
    apply_sql_file(engine, "views.sql")
    print(f"Loaded {written} rows into {DB_SCHEMA}.orders and refreshed views")


if __name__ == "__main__":
    main()
