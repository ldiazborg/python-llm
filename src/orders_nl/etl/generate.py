"""Generate a synthetic orders CSV with deliberate data-quality problems (requirement 1).

Usage: uv run orders-generate --rows 1200 --seed 42 --out data/orders_raw.csv
"""

import argparse
from pathlib import Path

import pandas as pd

COLUMNS = ["order_id", "order_date", "customer", "country", "product", "amount", "status"]


def generate_orders(
    n: int = 1200,
    *,
    seed: int = 42,
    duplicate_rate: float = 0.03,
    bad_date_rate: float = 0.05,
    missing_amount_rate: float = 0.04,
) -> pd.DataFrame:
    """Return a raw DataFrame with `COLUMNS`, all values as strings (as read from a messy CSV).

    Must be deterministic for a given `seed` (use `random.Random(seed)` / numpy Generator).

    Deliberate errors to inject:
      - exact duplicate rows (~duplicate_rate of n), appended so total rows >= n
      - badly written dates (~bad_date_rate): mixed formats like "15/03/2025",
        "2025/3/15", "March 15 2025", plus a few unparseable ones ("2025-13-45", "")
      - empty amounts (~missing_amount_rate)
    Optional extras worth considering: stray whitespace / casing in country names,
    amounts with comma decimal separator ("12,50").
    """
    raise NotImplementedError("TODO: implement generate_orders")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/orders_raw.csv"))
    args = parser.parse_args(argv)

    df = generate_orders(args.rows, seed=args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
