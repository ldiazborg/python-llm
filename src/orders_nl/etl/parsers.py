"""Scalar parsers for messy raw values. Pure functions: no pandas, trivially testable.

Each parser returns the clean value, or None when the input can't be trusted,
leaving the decision of what to do with bad rows to the caller.

    parse_date("15/03/2025")      -> date(2025, 3, 15)
    parse_amount("1.234,56")      -> Decimal("1234.56")
    normalize_spaces("  Ana  Gil") -> "Ana Gil"
    fold("ESPAÑA")                -> "espana"   (comparison key)

`fold` is also reused outside the ETL (answers/grounding.py) to match names in
LLM sentences regardless of case and accents.
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

# Rounding unit for money: two decimal places.
_CENT = Decimal("0.01")
# Characters stripped from both ends of an amount before parsing.
_CURRENCY_CHARS = "€$  "  # symbols and (non-breaking) spaces around amounts


def parse_date(value: str | None, formats: Iterable[str] = DATE_FORMATS) -> date | None:
    """Parse a date written in any of `formats`; None if blank or invalid (e.g. 31/02).

    Args:
        value: raw text from the CSV (None is treated as blank).
        formats: `strptime` patterns to try, first match wins.

    Returns:
        The date, or None if no format matches. Timestamps lose their time part.
    """
    text = (value or "").strip()
    if not text:
        return None
    for fmt in formats:
        try:
            # strptime also validates the calendar: 31/02/2025 raises ValueError.
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue  # not this format; try the next one
    return None


def parse_amount(value: str | None) -> Decimal | None:
    """Parse a money amount into a 2-decimal Decimal; None if blank or not a number.

    Handles decimal commas ("12,50"), thousands separators in both styles
    ("1.234,56" and "1,234.56") and currency symbols ("€ 12.50").

    The sign is kept ("-5" -> Decimal("-5.00")): rejecting negative amounts is
    a business rule, applied by the cleaner, not a parsing concern.

    Note: a lone comma is always read as a decimal separator ("1,234" ->
    1.234), the Spanish convention used by the data.
    """
    text = (value or "").strip().strip(_CURRENCY_CHARS)
    if not text:
        return None
    if "," in text and "." in text:
        # Whichever separator comes last is the decimal one.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")  # 1.234,56 -> 1234.56
        else:
            text = text.replace(",", "")  # 1,234.56 -> 1234.56
    elif "," in text:
        text = text.replace(",", ".")  # 12,50 -> 12.50
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None  # not a number at all ("abc", "12..5")
    if not amount.is_finite():  # rejects "NaN" / "Infinity", which Decimal accepts
        return None
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def normalize_spaces(value: str | None) -> str:
    """Trim and collapse internal runs of whitespace to one space."""
    return " ".join((value or "").split())


def fold(value: str) -> str:
    """Comparison key: case-insensitive and accent-insensitive ("ESPAÑA" == "espana").

    How: casefold() lower-cases aggressively, NFKD splits "ñ" into "n" + a
    combining tilde, and the combining marks are then dropped.
    """
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def build_canonicalizer(canonical: Iterable[str]):
    """Return a function mapping any spelling of a known value to its canonical form.

    Unknown values are kept (whitespace-normalized) rather than dropped, so new
    countries in real data are not lost; they just won't be merged with variants.

    Example:
        to_country = build_canonicalizer(["España", "México"])
        to_country("  MEXICO ")  -> "México"
        to_country("Perú")       -> "Perú"   (unknown: kept as is)
    """
    # folded spelling -> canonical spelling, built once per canonicalizer.
    lookup = {fold(name): name for name in canonical}

    def canonicalize(value: str | None) -> str:
        """Canonical form of `value`, or `value` itself (space-normalized) if unknown."""
        text = normalize_spaces(value)
        return lookup.get(fold(text), text)

    return canonicalize
