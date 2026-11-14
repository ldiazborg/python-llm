"""Generate valid synthetic orders with Faker, then apply defect injectors."""

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
from faker import Faker

from orders_nl.domain import COLUMNS, OrderStatus
from orders_nl.generation.catalog import (
    DEFAULT_COUNTRIES,
    DEFAULT_PRODUCTS,
    DEFAULT_QUANTITY_WEIGHTS,
    DEFAULT_STATUS_WEIGHTS,
    CountrySpec,
    ProductSpec,
)
from orders_nl.generation.defects import Defect, default_defects

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class GeneratorConfig:
    """Everything that shapes the generated dataset. Same config + seed => same data.

    rows:                  valid orders to create (defects such as duplicates add more)
    customers_per_country: size of the repeat-customer pool per country
    customer_skew:         >0 makes a few customers buy much more (Zipf-like),
                           so "top customers" is meaningful; 0 = uniform
    """

    rows: int = 1200
    seed: int = 42
    start_date: date = date(2025, 1, 1)
    end_date: date = date(2025, 12, 31)
    customers_per_country: int = 40
    customer_skew: float = 1.0
    countries: Sequence[CountrySpec] = DEFAULT_COUNTRIES
    products: Sequence[ProductSpec] = DEFAULT_PRODUCTS
    status_weights: dict[OrderStatus, float] = field(
        default_factory=lambda: dict(DEFAULT_STATUS_WEIGHTS)
    )
    quantity_weights: dict[int, float] = field(
        default_factory=lambda: dict(DEFAULT_QUANTITY_WEIGHTS)
    )

    def __post_init__(self) -> None:
        if self.rows < 1:
            raise ValueError("rows must be >= 1")
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if not self.countries or not self.products:
            raise ValueError("countries and products must not be empty")


def generate_orders(
    config: GeneratorConfig | None = None,
    defects: Sequence[Defect] | None = None,
) -> pd.DataFrame:
    """Return a raw orders DataFrame: valid orders + injected defects, all as strings.

    `defects=None` applies `default_defects()`; pass `[]` for a clean dataset.
    Defects run in the given order on a shared RNG, so the result is reproducible.
    """
    config = config or GeneratorConfig()
    rng = random.Random(config.seed)

    df = _generate_valid_orders(config, rng)
    for defect in default_defects() if defects is None else defects:
        df = defect.apply(df, rng)
    return df.reset_index(drop=True)


# --- internals -----------------------------------------------------------------


def _generate_valid_orders(config: GeneratorConfig, rng: random.Random) -> pd.DataFrame:
    """Build `config.rows` well-formed orders (ISO dates, dot decimals, canonical text)."""
    customer_pools = _build_customer_pools(config)

    # Zipf-like weights: the customer at rank r is picked ~1/(r+1)^skew as often.
    pool_weights = [1 / (rank + 1) ** config.customer_skew for rank in range(config.customers_per_country)]
    country_weights = [c.weight for c in config.countries]
    statuses = list(config.status_weights)
    status_weights = list(config.status_weights.values())
    quantities = list(config.quantity_weights)
    quantity_weights = list(config.quantity_weights.values())
    span_days = (config.end_date - config.start_date).days

    records = []
    for i in range(1, config.rows + 1):
        country = rng.choices(config.countries, weights=country_weights)[0]
        customer = rng.choices(customer_pools[country.name], weights=pool_weights)[0]
        product = rng.choice(config.products)
        quantity = rng.choices(quantities, weights=quantity_weights)[0]
        records.append(
            {
                "order_id": f"ORD-{i:06d}",
                "order_date": (config.start_date + timedelta(days=rng.randint(0, span_days))).isoformat(),
                "customer": customer,
                "country": country.name,
                "product": product.name,
                "amount": str(_unit_price(product, rng) * quantity),
                "status": rng.choices(statuses, weights=status_weights)[0].value,
            }
        )
    # Strings only: the generator mimics what a CSV reader would see.
    return pd.DataFrame.from_records(records, columns=list(COLUMNS)).astype(str)


def _build_customer_pools(config: GeneratorConfig) -> dict[str, list[str]]:
    """Create a fixed list of unique customer names per country, using its locale."""
    pools: dict[str, list[str]] = {}
    for offset, country in enumerate(config.countries):
        fake = Faker(country.locale)
        # Distinct seed per country so pools don't mirror each other.
        fake.seed_instance(config.seed + offset)
        names: list[str] = []
        seen: set[str] = set()
        while len(names) < config.customers_per_country:
            name = fake.name()
            if name not in seen:
                seen.add(name)
                names.append(name)
        pools[country.name] = names
    return pools


def _unit_price(product: ProductSpec, rng: random.Random) -> Decimal:
    """Random price within the product's range, rounded to cents (Decimal: no float drift)."""
    raw = rng.uniform(float(product.min_price), float(product.max_price))
    return Decimal(str(raw)).quantize(_CENT, rounding=ROUND_HALF_UP)
