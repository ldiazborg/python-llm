"""Shared domain vocabulary: the single source of truth for the order record.

Generation, cleaning, loading and the LLM prompt all import from here, so a new
column, status or country is added in one place. Keep `sql/schema.sql` (CHECK
constraint) and `sql/views.sql` (sale statuses) in sync when changing statuses.

Who uses what:
    COLUMNS          generator output, cleaner contract, loader column order
    OrderStatus      generator weights, cleaner validation, prompt description
    SALE_STATUSES    prompt ("ventas" = completed) and, by convention, views.sql
    KNOWN_COUNTRIES  cleaner canonicalization, prompt, generator catalog check
"""

from enum import StrEnum

# Column order of the raw CSV and of the clean table (`analytics.orders`).
# `order_id` was added to the columns the brief asked for: it identifies
# duplicates and is the table's primary key.
COLUMNS: tuple[str, ...] = (
    "order_id",
    "order_date",
    "customer",
    "country",
    "product",
    "amount",
    "status",
)


class OrderStatus(StrEnum):
    """Lifecycle states an order can be in. Values are what is stored in the DB.

    A `StrEnum`, so members compare equal to their string values
    (`OrderStatus.COMPLETED == "completed"`) and drop into SQL/JSON unchanged.
    """

    COMPLETED = "completed"  # paid and delivered: the only status that is revenue
    PENDING = "pending"  # placed but not yet paid/fulfilled
    CANCELLED = "cancelled"  # cancelled before completion
    REFUNDED = "refunded"  # completed, then money returned


# Statuses that count as revenue ("ventas"). Pending orders are not yet paid and
# cancelled/refunded ones were never or no longer are revenue.
SALE_STATUSES: frozenset[OrderStatus] = frozenset({OrderStatus.COMPLETED})

# Canonical country names (Spanish, with accents). The cleaner maps messy
# spellings ("ESPANA", " españa ") onto these.
KNOWN_COUNTRIES: tuple[str, ...] = (
    "España",
    "México",
    "Argentina",
    "Colombia",
    "Chile",
    "Estados Unidos",
)
