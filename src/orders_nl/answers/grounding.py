"""Grounding check: every number in an LLM answer must come from the data.

A small model may garble or invent figures ("6.512,32 €" instead of
"6.215,32 €", a made-up percentage). This module extracts the numbers in a
sentence and verifies each one against the values the answer may legitimately
use. Pure functions, no I/O.

Numbers are compared as Decimals under every plausible reading, because the
same token means different things in Spanish and English notation:
"1.138" is 1138 (es) or 1.138 (en); "6.215,32" is 6215.32.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

# Accent/case-insensitive matching of names ("Mexico" finds "México"). A general
# text helper that happens to live with the ETL parsers.
from orders_nl.etl.parsers import fold

# A run of digits, optionally with internal . or , separators ("6.215,32").
_NUMBER_TOKEN = re.compile(r"\d(?:[\d.,]*\d)?")
_THOUSANDS_DOT = re.compile(r"\d{1,3}(?:\.\d{3})+")  # 1.234 / 12.345.678
_THOUSANDS_COMMA = re.compile(r"\d{1,3}(?:,\d{3})+")  # 1,234 / 12,345,678

# A number in the answer also matches a data value rounded to these places,
# so "unos 6.215 €" is accepted for 6215.32.
_ROUNDING_STEPS = (Decimal("1"), Decimal("0.1"), Decimal("0.01"))


def _to_decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def interpretations(token: str) -> set[Decimal]:
    """Every plausible numeric value of `token` under Spanish or English notation."""
    candidates: list[str] = []
    if "." in token and "," in token:
        # Both separators: whichever comes last is the decimal one.
        if token.rfind(",") > token.rfind("."):
            candidates.append(token.replace(".", "").replace(",", "."))
        else:
            candidates.append(token.replace(",", ""))
    elif "." in token:
        candidates.append(token)  # decimal point
        if _THOUSANDS_DOT.fullmatch(token):
            candidates.append(token.replace(".", ""))  # Spanish thousands
    elif "," in token:
        candidates.append(token.replace(",", "."))  # Spanish decimal comma
        if _THOUSANDS_COMMA.fullmatch(token):
            candidates.append(token.replace(",", ""))  # English thousands
    else:
        candidates.append(token)
    return {value for c in candidates if (value := _to_decimal(c)) is not None}


def numbers_in_text(text: str) -> list[tuple[str, set[Decimal]]]:
    """(token, possible values) for each number written in `text`."""
    found = []
    for match in _NUMBER_TOKEN.finditer(text):
        values = interpretations(match.group())
        if values:  # skip things like version strings that aren't numbers
            found.append((match.group(), values))
    return found


def _values_in(value: Any) -> set[Decimal]:
    """Numeric values contained in one cell (numbers, or digits inside text/dates)."""
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float, Decimal)):
        decimal = _to_decimal(str(value))
        return {decimal} if decimal is not None else set()
    # Strings, dates...: "2025-03-01" contributes 2025, 3 and 1.
    return {v for _, values in numbers_in_text(str(value)) for v in values}


def allowed_numbers(
    rows: Iterable[Mapping[str, Any]],
    *texts: str,
    row_count: int,
) -> set[Decimal]:
    """Numbers an answer may mention.

    - every value in the result rows
    - numbers in `texts` (the question and the executed SQL: "top 3", "2025",
      `month = 3`), which the answer may legitimately repeat
    - 0..row_count, for counts and enumerations ("6 países", "1. ..., 2. ...")
    """
    allowed: set[Decimal] = set()
    for row in rows:
        for value in row.values():
            allowed |= _values_in(value)
    for text in texts:
        allowed |= _values_in(text)
    allowed |= {Decimal(i) for i in range(row_count + 1)}
    return allowed


def _with_roundings(values: Iterable[Decimal]) -> set[Decimal]:
    """Absolute values plus their roundings to 0, 1 and 2 decimal places."""
    expanded: set[Decimal] = set()
    for value in values:
        value = abs(value)
        expanded.add(value)
        for step in _ROUNDING_STEPS:
            try:
                expanded.add(value.quantize(step, rounding=ROUND_HALF_UP))
            except InvalidOperation:  # value too large for the context precision
                continue
    return expanded


def find_unsupported_numbers(answer: str, allowed: Iterable[Decimal]) -> list[str]:
    """Number tokens in `answer` that match no allowed value (empty list = grounded)."""
    accepted = _with_roundings(allowed)
    return [token for token, values in numbers_in_text(answer) if not (values & accepted)]


# --- attribution: is each number next to the right name? ------------------------------
#
# find_unsupported_numbers only proves a number exists somewhere in the data. A
# model can still pair real numbers with the wrong rows, e.g. a reversed ranking
# ("1. <customer ranked 3rd> ... 13.127 €"). The checks below bind numbers to the
# row named next to them:
#   - a number binds to the closest name *before* it in the same sentence
#     ("En Chile hay 8 pedidos"),
#   - a list marker ("1." / "2)") at the start of a line binds to the name right
#     *after* it ("1. Albano ...").
# A bound number must belong to that name's row(s), or to the question/SQL.
# A list marker is a claim about *position*, so it must instead be a value of
# that row (e.g. its rank) or the row's actual 1-based position in the result;
# the question/SQL don't count for markers ("top 3" must not excuse "3. <1st>").
# Numbers with no name to bind to are left to find_unsupported_numbers.

_LIST_MARKER = re.compile(r"(?m)^\s*(\d+)[.)]\s")
# Sentence ends: newline, or ". " not preceded by a digit (so "1. Albano" and
# "6.215,32" never split a sentence).
_SENTENCE_BREAK = re.compile(r"\n|(?<=[^\d])\.\s")
_MIN_NAME_LENGTH = 3  # ignore very short text values that would match anywhere


@dataclass(frozen=True)
class _Mention:
    start: int
    end: int
    values: frozenset[Decimal]  # numbers in the row(s) this name belongs to
    positions: frozenset[Decimal]  # 1-based positions of those rows in the result


def _name_mentions(folded_answer: str, rows: Iterable[Mapping[str, Any]]) -> list[_Mention]:
    """Where row names (text cells) appear in the answer, longest names first."""
    # name -> numbers / positions of every row it appears in ("España" in 12 monthly rows).
    values_by_name: dict[str, set[Decimal]] = {}
    positions_by_name: dict[str, set[Decimal]] = {}
    for position, row in enumerate(rows, start=1):
        row_values = {v for cell in row.values() for v in _values_in(cell)}
        for cell in row.values():
            if isinstance(cell, str) and len(cell.strip()) >= _MIN_NAME_LENGTH:
                name = fold(cell.strip())
                values_by_name.setdefault(name, set()).update(row_values)
                positions_by_name.setdefault(name, set()).add(Decimal(position))

    mentions: list[_Mention] = []
    taken: list[tuple[int, int]] = []
    # Longest first so "Estados Unidos" wins over a shorter name inside it.
    for name in sorted(values_by_name, key=len, reverse=True):
        for match in re.finditer(re.escape(name), folded_answer):
            span = (match.start(), match.end())
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue  # overlaps a longer name already found
            taken.append(span)
            mentions.append(
                _Mention(span[0], span[1], frozenset(values_by_name[name]), frozenset(positions_by_name[name]))
            )
    return sorted(mentions, key=lambda m: m.start)


def _sentence_of(position: int, breaks: list[int]) -> int:
    """Index of the sentence containing `position`."""
    return sum(1 for b in breaks if b < position)


def find_misattributed_numbers(answer: str, rows: list[Mapping[str, Any]], *texts: str) -> list[str]:
    """Number tokens placed next to a row they don't belong to (empty list = consistent).

    `texts` (question, SQL) hold numbers the answer may repeat anywhere.
    """
    folded = fold(answer)  # same length as `answer` for Spanish text; positions stay aligned
    mentions = _name_mentions(folded, rows)
    if not mentions:
        return []
    breaks = [m.start() for m in _SENTENCE_BREAK.finditer(folded)]
    context = {v for text in texts for v in _values_in(text)}
    markers = {m.start(1) for m in _LIST_MARKER.finditer(folded)}

    wrong: list[str] = []
    for match in _NUMBER_TOKEN.finditer(folded):
        values = interpretations(match.group())
        if not values or any(m.start <= match.start() < m.end for m in mentions):
            continue
        sentence = _sentence_of(match.start(), breaks)
        if match.start() in markers:
            # "1. Name": the marker belongs to the next name on its line and
            # must be that row's rank (a value of the row) or its position.
            candidates = [m for m in mentions if m.start >= match.end() and _sentence_of(m.start, breaks) == sentence]
            bound = candidates[0] if candidates else None
            accepted = (bound.values | bound.positions) if bound else frozenset()
        else:
            # "Name ... 8": the closest name before the number, same sentence.
            candidates = [m for m in mentions if m.end <= match.start() and _sentence_of(m.start, breaks) == sentence]
            bound = candidates[-1] if candidates else None
            accepted = (bound.values | context) if bound else frozenset()
        if bound is not None and not (values & _with_roundings(accepted)):
            wrong.append(match.group())
    return wrong
