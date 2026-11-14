"""Clean the raw orders data (requirement 1). Pure functions: no I/O, easy to test."""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class CleaningReport:
    rows_in: int
    duplicates_removed: int
    dates_repaired: int
    rows_dropped_bad_date: int
    rows_dropped_missing_amount: int
    rows_out: int


def clean_orders(raw: pd.DataFrame) -> tuple[pd.DataFrame, CleaningReport]:
    """Return (clean_df, report).

    Contract for clean_df (must match orders_nl/sql/schema.sql):
      - columns: order_id, order_date, customer, country, product, amount, status
      - no duplicate rows; order_id unique
      - order_date: datetime.date (parsed from the mixed formats; unparseable -> dropped)
      - amount: float/Decimal >= 0, no NaN (decide: drop rows vs. impute; document in README)
      - text columns stripped, consistent casing for country/status
    """
    raise NotImplementedError("TODO: implement clean_orders")
