"""Deterministic Spanish answers: the fallback when the LLM sentence can't be used.

Plain, exact and instant. They read mechanically, but never misstate a number.

Shapes, chosen by the size of the result:

    0 rows             "No se encontraron resultados para esta pregunta."
    1 row, 1 column    "El resultado es 6.215,32 (total_sales)."
    1 row, n columns   "Resultado: product: Portátil, total_sales: 22.481,85."
    n rows             "Se encontraron 6 resultados: Chile (pending_orders 8); ...; y 1 más."
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from orders_nl.db import QueryResult

# Swap separators: "6,215.32" (Python's format) -> "6.215,32" (Spanish).
_TO_SPANISH = str.maketrans(",.", ".,")


def format_number(value: int | float | Decimal) -> str:
    """Spanish number format: thousands with '.', decimals with ',' (2 places).

    Examples: 1138 -> "1.138", 6215.32 -> "6.215,32", Decimal("0.5") -> "0,50".
    Integers (counts) keep no decimals; everything else is shown as money.
    """
    if isinstance(value, int):
        return f"{value:,}".translate(_TO_SPANISH)
    return f"{float(value):,.2f}".translate(_TO_SPANISH)


def format_value(value: Any) -> str:
    """Human-readable cell value."""
    if value is None:
        return "sin datos"
    # bool before numbers: bool is a subclass of int in Python.
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, (int, float, Decimal)):
        return format_number(value)
    # datetime before date: datetime is a subclass of date.
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _describe_row(row: dict[str, Any], columns: list[str]) -> str:
    """'Chile (pending_orders 8)': a name as the label, the other columns in brackets.

    The label is the first text column (customer, country, product...), not
    simply the first column, so ranked results read "Albano (customer_rank 1,
    ...)" instead of "1 (customer Albano, ...)".
    """
    # next(generator, default): first text column, or the first column if none.
    label_column = next((c for c in columns if isinstance(row.get(c), str)), columns[0])
    label = format_value(row.get(label_column))
    details = ", ".join(f"{c} {format_value(row.get(c))}" for c in columns if c != label_column)
    return f"{label} ({details})" if details else label


def template_answer(result: QueryResult, *, max_items: int = 5) -> str:
    """Describe `result` in one Spanish sentence, choosing a shape by its size.

    Args:
        result: the query result.
        max_items: rows listed before summarizing the rest as "y N más".
    """
    rows = result.rows
    if not rows:
        return "No se encontraron resultados para esta pregunta."
    # Prefer the cursor's column order; fall back to the first row's keys.
    columns = result.columns or list(rows[0])
    if not columns:
        # `SELECT FROM orders` is valid PostgreSQL: rows without any column.
        # There is nothing to describe, and the shapes below need a column.
        return "No se encontraron resultados para esta pregunta."

    if len(rows) == 1 and len(columns) == 1:
        value = rows[0].get(columns[0])
        if value is None:  # e.g. SUM over no matching rows
            return "No hay datos para esta pregunta."
        return f"El resultado es {format_value(value)} ({columns[0]})."

    if len(rows) == 1:
        pairs = ", ".join(f"{c}: {format_value(rows[0].get(c))}" for c in columns)
        return f"Resultado: {pairs}."

    items = "; ".join(_describe_row(row, columns) for row in rows[:max_items])
    more = f"; y {len(rows) - max_items} más" if len(rows) > max_items else ""
    return f"Se encontraron {len(rows)} resultados: {items}{more}."
