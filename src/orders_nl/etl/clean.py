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

Adding a step, e.g. dropping future dates:

    def drop_future_dates(df, report):
        keep = df["order_date"] <= date.today()
        return df.loc[keep]

    clean_orders(raw, steps=(*DEFAULT_STEPS, drop_future_dates))

Steps that parse values (dates, amounts) do the per-value work with the pure
functions in `parsers.py`; this module only applies them to whole columns.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from orders_nl.domain import COLUMNS, KNOWN_COUNTRIES, OrderStatus
from orders_nl.etl.parsers import build_canonicalizer, normalize_spaces, parse_amount, parse_date


@dataclass
class CleaningReport:
    """Counters filled in by the cleaning steps; printed by the loader.

    Mutable on purpose: each step increments the counters it is responsible
    for. Invariant after a full run with the default steps:

        rows_out == rows_in - duplicates_removed
                   - rows_dropped_bad_date - rows_dropped_bad_amount
                   - rows_dropped_invalid_status
    """

    rows_in: int = 0  # rows in the raw input
    duplicates_removed: int = 0  # exact copies + repeated order_ids
    text_values_normalized: int = 0  # cells whose text changed (spaces, case, accents)
    dates_repaired: int = 0  # valid dates that weren't ISO yet (e.g. 15/03/2025)
    amounts_repaired: int = 0  # valid amounts that needed reformatting (e.g. "12,50")
    rows_dropped_bad_date: int = 0  # blank or impossible dates
    rows_dropped_bad_amount: int = 0  # blank, non-numeric or negative amounts
    rows_dropped_invalid_status: int = 0  # status outside OrderStatus
    rows_out: int = 0  # rows in the clean output


# Signature every cleaning step implements.
CleaningStep = Callable[[pd.DataFrame, CleaningReport], pd.DataFrame]

# Built once at import: maps "ESPANA", " españa " ... to "España".
_canonical_country = build_canonicalizer(KNOWN_COUNTRIES)
# Plain strings ("completed", ...) for fast membership checks with Series.isin.
_VALID_STATUSES = frozenset(s.value for s in OrderStatus)


# --- steps -----------------------------------------------------------------------


def normalize_text(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Trim/collapse whitespace everywhere, canonical countries, lower-case statuses."""
    out = df.copy()
    # Column-specific normalizers; every other column just gets whitespace cleanup.
    normalizers: dict[str, Callable[[str], str]] = {
        "country": _canonical_country,
        "status": lambda v: normalize_spaces(v).lower(),
    }
    for column in COLUMNS:
        before = out[column]
        out[column] = before.map(normalizers.get(column, normalize_spaces))
        # Count changed cells (element-wise comparison, then sum of True values).
        report.text_values_normalized += int((before != out[column]).sum())
    return out


def drop_exact_duplicates(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Remove rows identical in every column (after text normalization)."""
    out = df.drop_duplicates(keep="first")
    report.duplicates_removed += len(df) - len(out)
    return out


def parse_dates(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Convert order_date to `datetime.date`; drop rows whose date can't be parsed."""
    parsed = df["order_date"].map(parse_date)  # date or None per row
    valid = parsed.notna()
    # "Repaired" = parsed fine but wasn't already written as ISO YYYY-MM-DD.
    iso = parsed[valid].map(date.isoformat)
    report.dates_repaired += int((iso != df.loc[valid, "order_date"]).sum())
    report.rows_dropped_bad_date += int((~valid).sum())

    out = df.loc[valid].copy()
    # Real date objects from here on: the loader writes them to a `date` column.
    out["order_date"] = parsed[valid]
    return out


def parse_amounts(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Convert amount to a 2-decimal float; drop blank, non-numeric or negative amounts."""
    parsed = df["amount"].map(parse_amount)  # Decimal or None per row
    valid = parsed.map(lambda a: a is not None and a >= 0)
    # "Repaired" = valid but needed reformatting (e.g. "12,50" -> 12.50).
    canonical = parsed[valid].map(lambda a: f"{a:.2f}")
    report.amounts_repaired += int((canonical != df.loc[valid, "amount"]).sum())
    report.rows_dropped_bad_amount += int((~valid).sum())

    out = df.loc[valid].copy()
    # float for pandas-friendliness; values are already rounded to cents and the
    # DB column is numeric(12, 2), so no precision is lost on load.
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

    Args:
        raw: raw orders; must contain every column in `domain.COLUMNS` (extra
            columns are ignored).
        steps: cleaning steps to run, in order.

    Returns:
        (clean DataFrame with a fresh 0..n-1 index, report of what was done).

    Raises:
        ValueError: if a required column is missing.
    """
    # Fail with a clear message instead of a KeyError deep inside a step.
    missing = set(COLUMNS) - set(raw.columns)
    if missing:
        raise ValueError(f"Raw data is missing columns: {sorted(missing)}")

    report = CleaningReport(rows_in=len(raw))
    # Work on strings only; NaN (e.g. CSV read without keep_default_na=False) -> "".
    # .loc[:, COLUMNS] also fixes the column order and drops unknown columns.
    df = raw.loc[:, list(COLUMNS)].fillna("").astype(str)
    for step in steps:
        df = step(df, report)

    df = df.reset_index(drop=True)
    report.rows_out = len(df)
    return df, report
