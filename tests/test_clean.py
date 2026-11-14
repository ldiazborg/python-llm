"""Cleaning tests (requirement 4): a hand-made dirty sample plus generator output."""

from datetime import date

import pandas as pd
import pytest

from orders_nl.etl.clean import CleaningReport, clean_orders
from orders_nl.domain import COLUMNS, KNOWN_COUNTRIES, OrderStatus
from orders_nl.generation import GeneratorConfig, generate_orders


@pytest.fixture
def raw() -> pd.DataFrame:
    rows = [
        ["A1", "2025-03-15", "Ana López", "España", "Laptop", "100.50", "completed"],
        ["A1", "2025-03-15", "Ana López", "España", "Laptop", "100.50", "completed"],  # duplicate
        ["A2", "16/03/2025", "Luis Pérez", " españa ", "Mouse", "20", "completed"],  # dd/mm/yyyy, messy country
        ["A3", "2025-13-45", "Eva Ruiz", "México", "Monitor", "250", "pending"],  # impossible date
        ["A4", "2025-03-17", "Juan Gil", "Chile", "Teclado", "", "completed"],  # empty amount
        ["A5", "", "Rosa Díaz", "Perú", "Laptop", "900", "cancelled"],  # empty date
    ]
    return pd.DataFrame(rows, columns=list(COLUMNS), dtype=str)


def test_removes_duplicates(raw: pd.DataFrame) -> None:
    clean, report = clean_orders(raw)
    assert clean["order_id"].is_unique
    assert report.duplicates_removed == 1


def test_parses_mixed_date_formats_and_drops_unparseable(raw: pd.DataFrame) -> None:
    clean, _ = clean_orders(raw)
    by_id = clean.set_index("order_id")
    assert by_id.loc["A2", "order_date"] == date(2025, 3, 16)
    assert "A3" not in by_id.index
    assert "A5" not in by_id.index
    assert all(isinstance(d, date) for d in clean["order_date"])


def test_no_missing_amounts_and_normalized_text(raw: pd.DataFrame) -> None:
    clean, report = clean_orders(raw)
    assert clean["amount"].notna().all()
    assert (clean["amount"] >= 0).all()
    assert set(clean.loc[clean["order_id"].isin(["A1", "A2"]), "country"]) == {"España"}
    assert report.rows_in == len(raw)
    assert report.rows_out == len(clean)
    assert tuple(clean.columns) == COLUMNS


def test_repairs_comma_decimal_amounts() -> None:
    raw = pd.DataFrame([["B1", "2025-01-02", "Ana", "Chile", "Ratón", "12,50", "completed"]], columns=list(COLUMNS))
    clean, report = clean_orders(raw)
    assert clean.loc[0, "amount"] == 12.5
    assert report.amounts_repaired == 1


def test_drops_unknown_status() -> None:
    raw = pd.DataFrame([["C1", "2025-01-02", "Ana", "Chile", "Ratón", "10", "lost"]], columns=list(COLUMNS))
    clean, report = clean_orders(raw)
    assert clean.empty
    assert report.rows_dropped_invalid_status == 1


def test_custom_steps_replace_the_default_pipeline(raw: pd.DataFrame) -> None:
    """Only the given steps run: here nothing is parsed, so nothing is dropped."""

    def tag(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
        return df.assign(product="X")

    clean, report = clean_orders(raw, steps=[tag])
    assert len(clean) == len(raw)
    assert set(clean["product"]) == {"X"}


def test_rejects_raw_data_without_required_columns() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        clean_orders(pd.DataFrame({"order_id": ["A"]}))


def test_cleans_generated_dataset_end_to_end() -> None:
    """Whatever the generator breaks, the cleaner must fix or drop."""
    raw = generate_orders(GeneratorConfig(rows=1000, seed=11))
    clean, report = clean_orders(raw)

    assert clean["order_id"].is_unique
    assert all(isinstance(d, date) for d in clean["order_date"])
    assert set(clean["country"]) <= set(KNOWN_COUNTRIES)
    assert set(clean["status"]) <= {s.value for s in OrderStatus}
    assert (clean["amount"] >= 0).all()
    assert report.duplicates_removed == len(raw) - 1000  # every injected copy found
    assert report.dates_repaired > 0
    # Every input row is accounted for: kept, deduplicated or dropped for a reason.
    dropped = report.rows_dropped_bad_date + report.rows_dropped_bad_amount + report.rows_dropped_invalid_status
    assert report.rows_out == report.rows_in - report.duplicates_removed - dropped
    # The vast majority survives cleaning (>= 1000 required orders minus a few %).
    assert report.rows_out >= 900
