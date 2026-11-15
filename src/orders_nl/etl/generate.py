"""CLI: write a synthetic, deliberately dirty orders CSV (requirement 1).

Usage:
    uv run orders-generate --rows 1200 --seed 42 --out data/orders_raw.csv
    uv run orders-generate --clean          # valid data only, no defects

The generation logic lives in `orders_nl.generation`; this module only parses
arguments and writes the file.
"""

import argparse
from pathlib import Path

from orders_nl.generation import GeneratorConfig, default_defects, generate_orders


def build_parser() -> argparse.ArgumentParser:
    """Command-line options (separate function so it can be inspected or tested)."""
    parser = argparse.ArgumentParser(description="Generate a dirty orders CSV.")
    parser.add_argument("--rows", type=int, default=1200, help="valid orders before defects (default 1200)")
    parser.add_argument("--seed", type=int, default=42, help="random seed for reproducible output")
    parser.add_argument("--out", type=Path, default=Path("data/orders_raw.csv"), help="output CSV path")
    parser.add_argument("--clean", action="store_true", help="skip defect injection")
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point (`orders-generate`, declared in pyproject.toml).

    Args:
        argv: arguments to parse; None means sys.argv (tests can pass a list).
    """
    args = build_parser().parse_args(argv)

    config = GeneratorConfig(rows=args.rows, seed=args.seed)
    # [] = no defects; default_defects() = the standard dirty dataset.
    df = generate_orders(config, defects=[] if args.clean else default_defects())

    # Create data/ (or any output folder) on first run.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8 explicitly: names and countries contain accents.
    df.to_csv(args.out, index=False, encoding="utf-8")
    print(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
