"""Reference data the generator samples from: countries, products and statuses.

Everything here is plain, immutable data so it can be swapped for a custom
catalog via `GeneratorConfig` without touching the generation logic:

    GeneratorConfig(countries=(CountrySpec("Perú", "es", 10),), ...)

(Faker has no es_PE locale, so the generic Spanish "es" is used; a new country
must also be added to domain.KNOWN_COUNTRIES for the cleaner to keep it canonical.)

Weights are relative (they don't need to add up to 100): `random.choices`
normalizes them.
"""

from dataclasses import dataclass
from decimal import Decimal

from orders_nl.domain import OrderStatus


@dataclass(frozen=True)
class CountrySpec:
    """A country orders come from.

    name:   canonical name stored in the data (must be in domain.KNOWN_COUNTRIES)
    locale: Faker locale used to produce realistic customer names for it
    weight: relative share of orders (doesn't need to sum to 100)
    """

    name: str
    locale: str
    weight: float


@dataclass(frozen=True)
class ProductSpec:
    """A product and the unit-price range it sells for (EUR).

    Prices are Decimal so amounts are exact to the cent.
    """

    name: str
    min_price: Decimal
    max_price: Decimal

    def __post_init__(self) -> None:
        """Validate the price range as soon as the catalog entry is created."""
        # Fail fast on a bad catalog entry instead of producing odd amounts later.
        if not (Decimal(0) < self.min_price <= self.max_price):
            raise ValueError(f"Invalid price range for {self.name!r}")


# Spain dominates (home market), then Latin America and a small US share.
# Locales verified against faker.config.AVAILABLE_LOCALES.
DEFAULT_COUNTRIES: tuple[CountrySpec, ...] = (
    CountrySpec("España", "es_ES", 35),
    CountrySpec("México", "es_MX", 20),
    CountrySpec("Argentina", "es_AR", 15),
    CountrySpec("Colombia", "es_CO", 12),
    CountrySpec("Chile", "es_CL", 10),
    CountrySpec("Estados Unidos", "en_US", 8),
)

# Tech/office products with realistic EUR price ranges; product names in Spanish
# to match the language of the questions.
DEFAULT_PRODUCTS: tuple[ProductSpec, ...] = (
    ProductSpec("Portátil", Decimal("600"), Decimal("1500")),
    ProductSpec("Monitor", Decimal("120"), Decimal("400")),
    ProductSpec("Tablet", Decimal("200"), Decimal("900")),
    ProductSpec("Impresora", Decimal("80"), Decimal("350")),
    ProductSpec("Silla de oficina", Decimal("90"), Decimal("450")),
    ProductSpec("Auriculares", Decimal("25"), Decimal("250")),
    ProductSpec("Disco SSD", Decimal("50"), Decimal("250")),
    ProductSpec("Teclado", Decimal("20"), Decimal("120")),
    ProductSpec("Webcam", Decimal("30"), Decimal("150")),
    ProductSpec("Ratón", Decimal("10"), Decimal("60")),
)

# Relative frequency of each status; most orders complete successfully.
DEFAULT_STATUS_WEIGHTS: dict[OrderStatus, float] = {
    OrderStatus.COMPLETED: 75,
    OrderStatus.PENDING: 10,
    OrderStatus.CANCELLED: 10,
    OrderStatus.REFUNDED: 5,
}

# Units per order line -> relative weight (single-unit orders dominate).
DEFAULT_QUANTITY_WEIGHTS: dict[int, float] = {1: 70, 2: 20, 3: 10}
