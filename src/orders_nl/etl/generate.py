"""CLI: write a synthetic, deliberately dirty orders CSV (requirement 1).

Usage:
    uv run orders-generate --rows 1200 --seed 42 --out data/orders_raw.csv

The generation logic lives in `orders_nl.generation`; this module only parses
arguments and writes the file.
"""

import argparse
from pathlib import Path

from orders_nl.generation import GeneratorConfig, default_defects, generate_orders


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a dirty orders CSV.")
    parser.add_argument("--rows", type=int, default=1200, help="valid orders before defects (default 1200)")
    parser.add_argument("--seed", type=int, default=42, help="random seed for reproducible output")
    parser.add_argument("--out", type=Path, default=Path("data/orders_raw.csv"), help="output CSV path")
    parser.add_argument("--clean", action="store_true", help="skip defect injection")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    config = GeneratorConfig(rows=args.rows, seed=args.seed)
    df = generate_orders(config, defects=[] if args.clean else default_defects())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8 explicitly: names and countries contain accents.
    df.to_csv(args.out, index=False, encoding="utf-8")
    print(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
