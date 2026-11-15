"""Generator tests: size, reproducibility and that each defect is really injected."""

import re
from datetime import date

import pandas as pd
import pytest

from orders_nl.domain import COLUMNS, KNOWN_COUNTRIES, OrderStatus
from orders_nl.generation import GeneratorConfig, generate_orders
from orders_nl.generation.catalog import DEFAULT_COUNTRIES
from orders_nl.generation.defects import DuplicateRows, MalformedDates, MissingAmounts


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_iso_date(value: str) -> bool:
    """Strict YYYY-MM-DD (date.fromisoformat alone also accepts e.g. 20250315)."""
    if not _ISO.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


@pytest.fixture(scope="module")
def raw() -> pd.DataFrame:
    """One generated dataset shared by the module's tests."""
    return generate_orders(GeneratorConfig(rows=1000, seed=7))


def test_has_at_least_requested_rows_and_expected_columns(raw: pd.DataFrame) -> None:
    """At least the requested rows (duplicates add more) with the domain columns."""
    assert len(raw) >= 1000
    assert tuple(raw.columns) == COLUMNS


def test_is_reproducible_for_a_seed() -> None:
    """Same config and seed produce an identical DataFrame."""
    config = GeneratorConfig(rows=200, seed=123)
    pd.testing.assert_frame_equal(generate_orders(config), generate_orders(config))


def test_without_defects_data_is_valid() -> None:
    """With defects disabled every value is well-formed and within the domain."""
    clean = generate_orders(GeneratorConfig(rows=300, seed=1), defects=[])
    assert not clean.duplicated().any()
    assert clean["order_date"].map(_is_iso_date).all()
    assert pd.to_numeric(clean["amount"]).gt(0).all()
    assert set(clean["status"]) <= {s.value for s in OrderStatus}
    assert set(clean["country"]) <= set(KNOWN_COUNTRIES)


def test_injects_duplicates_bad_dates_and_missing_amounts(raw: pd.DataFrame) -> None:
    """The default defects really appear in the output."""
    assert raw.duplicated().sum() > 0
    assert (~raw["order_date"].map(_is_iso_date)).sum() > 0
    assert (raw["amount"].str.strip() == "").sum() > 0


def test_defects_are_composable() -> None:
    """Only the defects passed in are applied."""
    config = GeneratorConfig(rows=300, seed=2)
    only_dupes = generate_orders(config, defects=[DuplicateRows(rate=0.1)])
    assert len(only_dupes) == 330
    assert only_dupes["order_date"].map(_is_iso_date).all()

    only_dates = generate_orders(config, defects=[MalformedDates(rate=0.1), MissingAmounts(rate=0)])
    assert len(only_dates) == 300
    assert (~only_dates["order_date"].map(_is_iso_date)).sum() == 30


def test_catalog_countries_are_known_domain_countries() -> None:
    """Generated countries are ones the cleaner recognizes."""
    assert {c.name for c in DEFAULT_COUNTRIES} <= set(KNOWN_COUNTRIES)
