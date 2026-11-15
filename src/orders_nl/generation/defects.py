"""Defect injectors: each one corrupts a share of rows in one specific way.

A defect is any object with `name` and `apply(df, rng) -> df` (the `Defect`
protocol). They are composed as a list, so adding a new kind of dirty data is
a new class here, with no change to the generator itself.

Injectors return a new DataFrame and never mutate their input.

All randomness comes from the `rng` argument (a seeded `random.Random` shared
by the whole generation run), never from the global `random` module or numpy,
so the same seed always produces exactly the same dirty dataset.

Adding a defect, e.g. negative amounts:

    @dataclass(frozen=True)
    class NegativeAmounts:
        rate: float = 0.01
        name: str = "negative_amounts"

        def apply(self, df, rng):
            out = df.copy()
            for idx in _sample_index(out, self.rate, rng):
                out.at[idx, "amount"] = "-" + out.at[idx, "amount"]
            return out
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Protocol

import pandas as pd


class Defect(Protocol):
    """Contract for a defect injector.

    Structural typing: any class with these members qualifies, no inheritance
    needed.
    """

    # Short identifier, useful in logs and when composing defect lists.
    name: str

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        """Return a copy of `df` with this defect applied to some rows.

        Args:
            df: raw orders, all values as strings (columns from domain.COLUMNS).
            rng: the shared seeded random generator of the generation run.
        """
        ...


def _sample_index(df: pd.DataFrame, rate: float, rng: random.Random) -> list:
    """Pick round(rate * len(df)) distinct row labels (at least 1 when rate > 0).

    The "at least 1" guarantees every enabled defect is visible even in tiny
    datasets, which keeps the tests meaningful.
    """
    if rate <= 0 or df.empty:
        return []
    k = min(len(df), max(1, round(rate * len(df))))
    # rng.sample: k distinct labels, no repeats, so a row is hit at most once.
    return rng.sample(list(df.index), k)


@dataclass(frozen=True)
class MalformedDates:
    """Rewrite ISO dates in other formats; a share becomes impossible to parse.

    Only day-first numeric formats are used (Spanish convention), so a cleaner
    can repair them unambiguously. `unparseable_share` of the affected rows get
    values no cleaner should accept (impossible dates, placeholders, blanks).
    """

    rate: float = 0.06  # share of rows whose date is rewritten
    unparseable_share: float = 0.25  # of those, share that becomes garbage
    name: str = "malformed_dates"

    # Valid dates, just written differently: the cleaner must repair these.
    REPAIRABLE_FORMATS: ClassVar[tuple[str, ...]] = (
        "%d/%m/%Y",  # 15/03/2025
        "%d-%m-%Y",  # 15-03-2025
        "%d.%m.%Y",  # 15.03.2025
        "%Y/%m/%d",  # 2025/03/15
        "%Y%m%d",  # 20250315
        "%Y-%m-%dT%H:%M:%S",  # 2025-03-15T00:00:00 (timestamp export)
    )
    # Values that are not a real date: the cleaner must drop these rows.
    UNPARSEABLE_VALUES: ClassVar[tuple[str, ...]] = (
        "2025-13-45",  # month 13, day 45
        "31/02/2025",  # 31 February
        "N/A",
        "sin fecha",
        "",
    )

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        """Reformat or destroy the order_date of a sample of rows."""
        out = df.copy()
        for idx in _sample_index(out, self.rate, rng):
            if rng.random() < self.unparseable_share:
                out.at[idx, "order_date"] = rng.choice(self.UNPARSEABLE_VALUES)
            else:
                # The generator always writes ISO dates, so parsing back is safe.
                parsed = date.fromisoformat(out.at[idx, "order_date"])
                out.at[idx, "order_date"] = parsed.strftime(rng.choice(self.REPAIRABLE_FORMATS))
        return out


@dataclass(frozen=True)
class MissingAmounts:
    """Blank out the amount (empty string or whitespace, as CSV exports do)."""

    rate: float = 0.04
    name: str = "missing_amounts"

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        """Replace the amount of a sample of rows with "" or " "."""
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
        """Swap the decimal point for a comma in a sample of amounts.

        Blank amounts (from MissingAmounts) are unaffected: "".replace() is "".
        """
        out = df.copy()
        for idx in _sample_index(out, self.rate, rng):
            out.at[idx, "amount"] = out.at[idx, "amount"].replace(".", ",")
        return out


@dataclass(frozen=True)
class MessyText:
    """Inconsistent casing, stray spaces and missing accents in text columns."""

    rate: float = 0.05  # share of rows affected, per column
    columns: tuple[str, ...] = ("country", "customer")
    name: str = "messy_text"

    # Strip Spanish accents without importing unicodedata for a tiny table.
    _ACCENTS: ClassVar[dict[int, str]] = str.maketrans("áéíóúñÁÉÍÓÚÑ", "aeiounAEIOUN")

    def apply(self, df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
        """Apply one random text corruption to a sample of cells in each column."""
        out = df.copy()
        # Each variant mimics a real-world data-entry problem.
        variants = (
            str.upper,  # "ESPAÑA"
            str.lower,  # "españa"
            lambda s: f"  {s} ",  # padding from a fixed-width export
            lambda s: s.translate(self._ACCENTS),  # "Espana": keyboard without ñ/accents
            lambda s: s.replace(" ", "  "),  # double spaces inside names
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
        """Return df plus round(rate * len(df)) duplicated rows, in shuffled order."""
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
    """The standard set of defects, in a safe order (duplicates last).

    Each rate is the share of rows the defect touches; 0 disables it.
    Order matters: MissingAmounts runs before CommaDecimalAmounts (which then
    skips blanks), and DuplicateRows runs last so copies are exact.
    """
    return (
        MalformedDates(rate=bad_date_rate),
        MissingAmounts(rate=missing_amount_rate),
        CommaDecimalAmounts(rate=comma_decimal_rate),
        MessyText(rate=messy_text_rate),
        DuplicateRows(rate=duplicate_rate),
    )
