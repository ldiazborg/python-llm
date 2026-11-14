"""Clean raw orders (requirement 1) as a pipeline of small, independent steps.

Each step is a function `(df, report) -> df` that fixes one kind of problem
and records what it did on the shared `CleaningReport`. `clean_orders` runs
`DEFAULT_STEPS` in order; pass your own `steps` to add, remove or reorder them.

Cleaning decisions (also in the README):
  - exact duplicates and repeated order_ids are removed, keeping the first
  - dates in known formats are repaired; unparseable dates drop the row
  - empty/invalid/negative amounts drop the row: imputing revenue would
    invent sales, and the row count lost is small and reported
  - country spellings are mapped to canonical names; statuses are lower-cased
    and rows with an unknown status are dropped (the DB enforces the set)
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from orders_nl.domain import COLUMNS, KNOWN_COUNTRIES, OrderStatus
from orders_nl.etl.parsers import build_canonicalizer, normalize_spaces, parse_amount, parse_date


@dataclass
class CleaningReport:
    """Counters filled in by the cleaning steps; printed by the loader."""

    rows_in: int = 0
    duplicates_removed: int = 0
    text_values_normalized: int = 0
    dates_repaired: int = 0
    amounts_repaired: int = 0
    rows_dropped_bad_date: int = 0
    rows_dropped_bad_amount: int = 0
    rows_dropped_invalid_status: int = 0
    rows_out: int = 0


CleaningStep = Callable[[pd.DataFrame, CleaningReport], pd.DataFrame]

_canonical_country = build_canonicalizer(KNOWN_COUNTRIES)
_VALID_STATUSES = frozenset(s.value for s in OrderStatus)


# --- steps -----------------------------------------------------------------------


def normalize_text(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Trim/collapse whitespace everywhere, canonical countries, lower-case statuses."""
    out = df.copy()
    normalizers: dict[str, Callable[[str], str]] = {
        "country": _canonical_country,
        "status": lambda v: normalize_spaces(v).lower(),
    }
    for column in COLUMNS:
        before = out[column]
        out[column] = before.map(normalizers.get(column, normalize_spaces))
        report.text_values_normalized += int((before != out[column]).sum())
    return out


def drop_exact_duplicates(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Remove rows identical in every column (after text normalization)."""
    out = df.drop_duplicates(keep="first")
    report.duplicates_removed += len(df) - len(out)
    return out


def parse_dates(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Convert order_date to `datetime.date`; drop rows whose date can't be parsed."""
    parsed = df["order_date"].map(parse_date)
    valid = parsed.notna()
    # "Repaired" = parsed fine but wasn't already written as ISO YYYY-MM-DD.
    iso = parsed[valid].map(date.isoformat)
    report.dates_repaired += int((iso != df.loc[valid, "order_date"]).sum())
    report.rows_dropped_bad_date += int((~valid).sum())

    out = df.loc[valid].copy()
    out["order_date"] = parsed[valid]
    return out


def parse_amounts(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Convert amount to a 2-decimal float; drop blank, non-numeric or negative amounts."""
    parsed = df["amount"].map(parse_amount)
    valid = parsed.map(lambda a: a is not None and a >= 0)
    # "Repaired" = valid but needed reformatting (e.g. "12,50" -> 12.50).
    canonical = parsed[valid].map(lambda a: f"{a:.2f}")
    report.amounts_repaired += int((canonical != df.loc[valid, "amount"]).sum())
    report.rows_dropped_bad_amount += int((~valid).sum())

    out = df.loc[valid].copy()
    out["amount"] = parsed[valid].map(float)
    return out


def drop_invalid_status(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Drop rows whose status is not one of `OrderStatus` (DB has a CHECK on it)."""
    valid = df["status"].isin(_VALID_STATUSES)
    report.rows_dropped_invalid_status += int((~valid).sum())
    return df.loc[valid]


def drop_duplicate_ids(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """order_id is the primary key: keep the first row of any repeated id."""
    out = df.drop_duplicates(subset="order_id", keep="first")
    report.duplicates_removed += len(df) - len(out)
    return out


# Order matters: normalize before de-duplicating so " España" and "España"
# copies collapse; parse before the id check so it only sees loadable rows.
DEFAULT_STEPS: tuple[CleaningStep, ...] = (
    normalize_text,
    drop_exact_duplicates,
    parse_dates,
    parse_amounts,
    drop_invalid_status,
    drop_duplicate_ids,
)


# --- pipeline ----------------------------------------------------------------------


def clean_orders(
    raw: pd.DataFrame,
    steps: Sequence[CleaningStep] = DEFAULT_STEPS,
) -> tuple[pd.DataFrame, CleaningReport]:
    """Run `steps` over `raw` and return (clean_df, report).

    The result has exactly `COLUMNS`, unique order_id, `datetime.date` dates,
    non-negative float amounts and canonical text, matching `sql/schema.sql`.
    `raw` is not modified.
    """
    missing = set(COLUMNS) - set(raw.columns)
    if missing:
        raise ValueError(f"Raw data is missing columns: {sorted(missing)}")

    report = CleaningReport(rows_in=len(raw))
    # Work on strings only; NaN (e.g. CSV read without keep_default_na=False) -> "".
    df = raw.loc[:, list(COLUMNS)].fillna("").astype(str)
    for step in steps:
        df = step(df, report)

    df = df.reset_index(drop=True)
    report.rows_out = len(df)
    return df, report
