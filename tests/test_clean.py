"""Cleaning tests (requirement 4): a hand-made dirty sample plus generator output."""

from datetime import date

import pandas as pd
import pytest

from orders_nl.etl.clean import CleaningReport, clean_orders
from orders_nl.etl.parsers import fold
from orders_nl.domain import COLUMNS, KNOWN_COUNTRIES, OrderStatus
from orders_nl.generation import GeneratorConfig, generate_orders


@pytest.fixture
def raw() -> pd.DataFrame:
    """Six hand-made rows covering one example of each defect."""
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
    """The exact duplicate is removed and counted once."""
    clean, report = clean_orders(raw)
    assert clean["order_id"].is_unique
    assert report.duplicates_removed == 1


def test_parses_mixed_date_formats_and_drops_unparseable(raw: pd.DataFrame) -> None:
    """dd/mm/yyyy is repaired; impossible and empty dates drop the row."""
    clean, _ = clean_orders(raw)
    by_id = clean.set_index("order_id")
    assert by_id.loc["A2", "order_date"] == date(2025, 3, 16)
    assert "A3" not in by_id.index
    assert "A5" not in by_id.index
    assert all(isinstance(d, date) for d in clean["order_date"])


def test_no_missing_amounts_and_normalized_text(raw: pd.DataFrame) -> None:
    """No blank amounts survive, countries are canonical, report totals match."""
    clean, report = clean_orders(raw)
    assert clean["amount"].notna().all()
    assert (clean["amount"] >= 0).all()
    assert set(clean.loc[clean["order_id"].isin(["A1", "A2"]), "country"]) == {"España"}
    assert report.rows_in == len(raw)
    assert report.rows_out == len(clean)
    assert tuple(clean.columns) == COLUMNS


def test_repairs_comma_decimal_amounts() -> None:
    """'12,50' becomes 12.5 and is counted as repaired."""
    raw = pd.DataFrame([["B1", "2025-01-02", "Ana", "Chile", "Ratón", "12,50", "completed"]], columns=list(COLUMNS))
    clean, report = clean_orders(raw)
    assert clean.loc[0, "amount"] == 12.5
    assert report.amounts_repaired == 1


def _orders_for(customers: list[str]) -> pd.DataFrame:
    """One valid order per customer name, so only the customer column varies."""
    rows = [[f"D{i}", "2025-01-02", name, "Chile", "Ratón", "10", "completed"] for i, name in enumerate(customers)]
    return pd.DataFrame(rows, columns=list(COLUMNS))


def test_unifies_customer_spellings() -> None:
    """Regression: case/accent variants of a name were kept as different customers."""
    raw = _orders_for(["Ana López", "Ana López", "ANA LÓPEZ", "ana lopez", "  Ana   López ", "Eva Ruiz", "JUAN GIL"])
    clean, report = clean_orders(raw)
    # The most frequent spelling wins; the other customers are untouched, and a
    # name only ever seen in capitals stays as written (nothing better is known).
    assert list(clean["customer"]) == ["Ana López"] * 5 + ["Eva Ruiz", "JUAN GIL"]
    assert report.text_values_normalized == 3  # the two variants + the padded one


def test_customer_spelling_ties_prefer_mixed_case_with_accents() -> None:
    """With one row each, the properly written name beats capitals and missing accents."""
    clean, _ = clean_orders(_orders_for(["MARÍA RUIZ", "Maria Ruiz", "María Ruiz", "maría ruiz"]))
    assert set(clean["customer"]) == {"María Ruiz"}


def test_drops_unknown_status() -> None:
    """A status outside OrderStatus drops the row and is counted."""
    raw = pd.DataFrame([["C1", "2025-01-02", "Ana", "Chile", "Ratón", "10", "lost"]], columns=list(COLUMNS))
    clean, report = clean_orders(raw)
    assert clean.empty
    assert report.rows_dropped_invalid_status == 1


def test_custom_steps_replace_the_default_pipeline(raw: pd.DataFrame) -> None:
    """Only the given steps run: here nothing is parsed, so nothing is dropped."""

    def tag(df: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
        """Toy step: overwrite every product name."""
        return df.assign(product="X")

    clean, report = clean_orders(raw, steps=[tag])
    assert len(clean) == len(raw)
    assert set(clean["product"]) == {"X"}


def test_rejects_raw_data_without_required_columns() -> None:
    """Missing columns fail early with a clear ValueError."""
    with pytest.raises(ValueError, match="missing columns"):
        clean_orders(pd.DataFrame({"order_id": ["A"]}))


def test_cleans_generated_dataset_end_to_end() -> None:
    """Whatever the generator breaks, the cleaner must fix or drop."""
    raw = generate_orders(GeneratorConfig(rows=1000, seed=11))
    clean, report = clean_orders(raw)

    assert clean["order_id"].is_unique
    assert all(isinstance(d, date) for d in clean["order_date"])
    assert set(clean["country"]) <= set(KNOWN_COUNTRIES)
    # One spelling per customer: no two names that differ only in case or accents.
    assert clean["customer"].map(fold).nunique() == clean["customer"].nunique()
    assert set(clean["status"]) <= {s.value for s in OrderStatus}
    assert (clean["amount"] >= 0).all()
    assert report.duplicates_removed == len(raw) - 1000  # every injected copy found
    assert report.dates_repaired > 0
    # Every input row is accounted for: kept, deduplicated or dropped for a reason.
    dropped = report.rows_dropped_bad_date + report.rows_dropped_bad_amount + report.rows_dropped_invalid_status
    assert report.rows_out == report.rows_in - report.duplicates_removed - dropped
    # The vast majority survives cleaning (>= 1000 required orders minus a few %).
    assert report.rows_out >= 900
