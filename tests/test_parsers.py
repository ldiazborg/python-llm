"""Unit tests for the scalar parsers used by the cleaner."""

from datetime import date
from decimal import Decimal

import pytest

from orders_nl.etl.parsers import build_canonicalizer, fold, normalize_spaces, parse_amount, parse_date


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2025-03-15", date(2025, 3, 15)),
        ("15/03/2025", date(2025, 3, 15)),
        ("15-03-2025", date(2025, 3, 15)),
        ("15.03.2025", date(2025, 3, 15)),
        ("2025/03/15", date(2025, 3, 15)),
        ("20250315", date(2025, 3, 15)),
        ("2025-03-15T10:30:00", date(2025, 3, 15)),
        ("  2025-03-15 ", date(2025, 3, 15)),
        ("03/04/2025", date(2025, 4, 3)),  # day-first
    ],
)
def test_parse_date_accepts_known_formats(raw: str, expected: date) -> None:
    """Every supported layout parses, day-first for numeric dates."""
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "N/A", "sin fecha", "2025-13-45", "31/02/2025", "04/15/2025"])
def test_parse_date_rejects_invalid(raw: str | None) -> None:
    """Blank, placeholder, impossible and month-first dates return None."""
    assert parse_date(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.5", Decimal("12.50")),
        ("12,50", Decimal("12.50")),
        ("1.234,56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("€ 99.999", Decimal("100.00")),
        (" 7 ", Decimal("7.00")),
        ("-5", Decimal("-5.00")),  # sign kept; the cleaner decides to drop it
    ],
)
def test_parse_amount(raw: str, expected: Decimal) -> None:
    """Decimal commas, both thousands styles, currency symbols and rounding."""
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", None, "abc", "NaN", "Infinity", "12..5"])
def test_parse_amount_rejects_invalid(raw: str | None) -> None:
    """Blank, non-numeric, NaN and Infinity return None."""
    assert parse_amount(raw) is None


def test_text_helpers() -> None:
    """Whitespace collapsing, accent/case folding and canonicalization."""
    assert normalize_spaces("  Ana   López ") == "Ana López"
    assert fold("ESPAÑA") == fold("espana")
    canonical = build_canonicalizer(["España", "México"])
    assert canonical("  MEXICO ") == "México"
    assert canonical("Perú") == "Perú"  # unknown values are kept
