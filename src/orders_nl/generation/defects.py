"""Defect injectors: each one corrupts a share of rows in one specific way.

A defect is any object with `name` and `apply(df, rng) -> df` (the `Defect`
protocol). They are composed as a list, so adding a new kind of dirty data is
a new class here, with no change to the generator itself.

Injectors return a new DataFrame and never mutate their input.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Protocol

import pandas as pd


class Defect(Protocol):
    """Contract for a defect injector."""

    name: str

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame: ...


def _sample_index(df: pd.DataFrame, rate: float, rng: random.Random) -> list:
    """Pick round(rate * len(df)) distinct row labels (at least 1 when rate > 0)."""
    if rate <= 0 or df.empty:
        return []
    k = min(len(df), max(1, round(rate * len(df))))
    return rng.sample(list(df.index), k)


@dataclass(frozen=True)
class MalformedDates:
    """Rewrite ISO dates in other formats; a share becomes impossible to parse.

    Only day-first numeric formats are used (Spanish convention), so a cleaner
    can repair them unambiguously. `unparseable_share` of the affected rows get
    values no cleaner should accept (impossible dates, placeholders, blanks).
    """

    rate: float = 0.06
    unparseable_share: float = 0.25
    name: str = "malformed_dates"

    REPAIRABLE_FORMATS: ClassVar[tuple[str, ...]] = (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S",
    )
    UNPARSEABLE_VALUES: ClassVar[tuple[str, ...]] = (
        "2025-13-45",
        "31/02/2025",
        "N/A",
        "sin fecha",
        "",
    )

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        out = df.copy()
        for idx in _sample_index(out, self.rate, rng):
            if rng.random() < self.unparseable_share:
                out.at[idx, "order_date"] = rng.choice(self.UNPARSEABLE_VALUES)
            else:
                parsed = date.fromisoformat(out.at[idx, "order_date"])
                out.at[idx, "order_date"] = parsed.strftime(rng.choice(self.REPAIRABLE_FORMATS))
        return out


@dataclass(frozen=True)
class MissingAmounts:
    """Blank out the amount (empty string or whitespace, as CSV exports do)."""

    rate: float = 0.04
    name: str = "missing_amounts"

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        out = df.copy()
        for idx in _sample_index(out, self.rate, rng):
            out.at[idx, "amount"] = rng.choice(("", " "))
        return out


@dataclass(frozen=True)
class CommaDecimalAmounts:
    """Write amounts with a decimal comma ("12,50"), common in Spanish locales."""

    rate: float = 0.03
    name: str = "comma_decimal_amounts"

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        out = df.copy()
        for idx in _sample_index(out, self.rate, rng):
            out.at[idx, "amount"] = out.at[idx, "amount"].replace(".", ",")
        return out


@dataclass(frozen=True)
class MessyText:
    """Inconsistent casing, stray spaces and missing accents in text columns."""

    rate: float = 0.05
    columns: tuple[str, ...] = ("country", "customer")
    name: str = "messy_text"

    # Strip Spanish accents without importing unicodedata for a tiny table.
    _ACCENTS: ClassVar[dict[int, str]] = str.maketrans("áéíóúñÁÉÍÓÚÑ", "aeiounAEIOUN")

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        out = df.copy()
        variants = (
            str.upper,
            str.lower,
            lambda s: f"  {s} ",
            lambda s: s.translate(self._ACCENTS),
            lambda s: s.replace(" ", "  "),
        )
        for column in self.columns:
            for idx in _sample_index(out, self.rate, rng):
                out.at[idx, column] = rng.choice(variants)(out.at[idx, column])
        return out


@dataclass(frozen=True)
class DuplicateRows:
    """Append exact copies of existing rows and shuffle them in.

    Should run last so the copies are byte-identical to already-defective rows,
    like a file that was exported twice.
    """

    rate: float = 0.03
    name: str = "duplicate_rows"

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        copies = df.loc[_sample_index(df, self.rate, rng)]
        combined = pd.concat([df, copies], ignore_index=True)
        # Deterministic shuffle driven by our RNG (not numpy's global state).
        order = list(range(len(combined)))
        rng.shuffle(order)
        return combined.iloc[order].reset_index(drop=True)


def default_defects(
    *,
    bad_date_rate: float = 0.06,
    missing_amount_rate: float = 0.04,
    comma_decimal_rate: float = 0.03,
    messy_text_rate: float = 0.05,
    duplicate_rate: float = 0.03,
) -> Sequence[Defect]:
    """The standard set of defects, in a safe order (duplicates last)."""
    return (
        MalformedDates(rate=bad_date_rate),
        MissingAmounts(rate=missing_amount_rate),
        CommaDecimalAmounts(rate=comma_decimal_rate),
        MessyText(rate=messy_text_rate),
        DuplicateRows(rate=duplicate_rate),
    )
