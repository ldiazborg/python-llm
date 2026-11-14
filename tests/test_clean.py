"""Cleaning tests (requirement 4) on a small hand-made dirty dataset."""

from datetime import date

import pandas as pd
import pytest

from conftest import todo
from orders_nl.etl.clean import clean_orders
from orders_nl.etl.generate import COLUMNS


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
    return pd.DataFrame(rows, columns=COLUMNS, dtype=str)


@todo
def test_removes_duplicates(raw: pd.DataFrame) -> None:
    clean, report = clean_orders(raw)
    assert clean["order_id"].is_unique
    assert report.duplicates_removed == 1


@todo
def test_parses_mixed_date_formats_and_drops_unparseable(raw: pd.DataFrame) -> None:
    clean, _ = clean_orders(raw)
    by_id = clean.set_index("order_id")
    assert by_id.loc["A2", "order_date"] == date(2025, 3, 16)
    assert "A3" not in by_id.index
    assert "A5" not in by_id.index
    assert all(isinstance(d, date) for d in clean["order_date"])


@todo
def test_no_missing_amounts_and_normalized_text(raw: pd.DataFrame) -> None:
    clean, report = clean_orders(raw)
    assert clean["amount"].notna().all()
    assert (clean["amount"] >= 0).all()
    assert set(clean.loc[clean["order_id"].isin(["A1", "A2"]), "country"]) == {"España"}
    assert report.rows_in == len(raw)
    assert report.rows_out == len(clean)
    assert list(clean.columns) == COLUMNS
