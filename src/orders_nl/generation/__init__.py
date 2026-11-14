"""Synthetic order data with deliberate data-quality defects.

Public API:
    generate_orders(config, defects)  -> raw DataFrame (all strings, like a messy CSV)
    GeneratorConfig                   -> what to generate (size, seed, catalogs, ...)
    default_defects(...)              -> the standard list of defect injectors

Extending: add a country/product to `catalog.py`, or a new defect class
implementing the `Defect` protocol in `defects.py` and pass it in `defects`.
"""

from orders_nl.generation.catalog import CountrySpec, ProductSpec
from orders_nl.generation.defects import Defect, default_defects
from orders_nl.generation.orders import GeneratorConfig, generate_orders

__all__ = [
    "CountrySpec",
    "Defect",
    "GeneratorConfig",
    "ProductSpec",
    "default_defects",
    "generate_orders",
]
