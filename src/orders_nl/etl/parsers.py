"""Scalar parsers for messy raw values. Pure functions: no pandas, trivially testable.

Each parser returns the clean value, or None when the input can't be trusted,
leaving the decision of what to do with bad rows to the caller.
"""

import unicodedata
from collections.abc import Iterable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Accepted date layouts, tried in order. Numeric dates are read day-first
# (Spanish convention): "03/04/2025" is 3 April. Month-first US dates such as
# "04/15/2025" therefore fail to parse instead of being silently misread.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%Y%m%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)

_CENT = Decimal("0.01")
_CURRENCY_CHARS = "€$  "  # symbols and (non-breaking) spaces around amounts


def parse_date(value: str | None, formats: Iterable[str] = DATE_FORMATS) -> date | None:
    """Parse a date written in any of `formats`; None if blank or invalid (e.g. 31/02)."""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str | None) -> Decimal | None:
    """Parse a money amount into a 2-decimal Decimal; None if blank or not a number.

    Handles decimal commas ("12,50"), thousands separators in both styles
    ("1.234,56" and "1,234.56") and currency symbols ("€ 12.50").
    """
    text = (value or "").strip().strip(_CURRENCY_CHARS)
    if not text:
        return None
    if "," in text and "." in text:
        # Whichever separator comes last is the decimal one.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():  # rejects "NaN" / "Infinity", which Decimal accepts
        return None
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def normalize_spaces(value: str | None) -> str:
    """Trim and collapse internal runs of whitespace to one space."""
    return " ".join((value or "").split())


def fold(value: str) -> str:
    """Comparison key: case-insensitive and accent-insensitive ("ESPAÑA" == "espana")."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def build_canonicalizer(canonical: Iterable[str]):
    """Return a function mapping any spelling of a known value to its canonical form.

    Unknown values are kept (whitespace-normalized) rather than dropped, so new
    countries in real data are not lost; they just won't be merged with variants.
    """
    lookup = {fold(name): name for name in canonical}

    def canonicalize(value: str | None) -> str:
        text = normalize_spaces(value)
        return lookup.get(fold(text), text)

    return canonicalize
