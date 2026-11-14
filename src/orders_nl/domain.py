"""Shared domain vocabulary: the single source of truth for the order record.

Generation, cleaning, loading and the LLM prompt all import from here, so a new
column, status or country is added in one place. Keep `sql/schema.sql` (CHECK
constraint) and `sql/views.sql` (sale statuses) in sync when changing statuses.
"""

from enum import StrEnum

# Column order of the raw CSV and of the clean table (`analytics.orders`).
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
    """Lifecycle states an order can be in. Values are what is stored in the DB."""

    COMPLETED = "completed"
    PENDING = "pending"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


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
