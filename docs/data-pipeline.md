# Data pipeline: generation, cleaning, loading

## Generation (`generation/`, CLI `etl/generate.py`)

- `generate_orders(GeneratorConfig, defects)` → DataFrame of **strings only** (mimics a raw CSV).
- Reproducibility: one `random.Random(config.seed)` drives generation and every defect, in a fixed order; Faker instances are seeded per country (`seed + offset`). Never use the global `random` module or numpy RNG in generation code — it breaks reproducibility tests.
- Customers: a fixed pool per country (Faker locale of that country) picked with Zipf-like weights (`customer_skew`), so `v_top_customers` has real repeat buyers.
- Amounts use `Decimal` (unit price × quantity) to stay exact to the cent.
- Defects (`generation/defects.py`): `MalformedDates`, `MissingAmounts`, `CommaDecimalAmounts`, `MessyText`, `DuplicateRows`. Each returns a copy, samples rows with `_sample_index` (at least 1 row when rate > 0). **`DuplicateRows` must run last** so copies are byte-identical; `MissingAmounts` runs before `CommaDecimalAmounts`.
- Malformed dates only use day-first numeric formats so the cleaner can repair them unambiguously; ~25% become unparseable on purpose.

## Cleaning (`etl/parsers.py`, `etl/clean.py`)

- `parsers.py`: pure scalar functions returning a value or `None` (never raising): `parse_date` (8 formats, day-first), `parse_amount` (decimal comma, both thousands styles, `€`; keeps the sign), `normalize_spaces`, `fold` (case/accent-insensitive key), `build_canonicalizer`.
- `clean.py`: `clean_orders(raw, steps=DEFAULT_STEPS)`; each step `(df, report) -> df` increments its own `CleaningReport` counters.
- Step order matters: `normalize_text` → `unify_customer_spellings` (needs the whitespace cleanup) → `drop_exact_duplicates` (after normalizing so variants collapse) → `parse_dates` → `parse_amounts` → `drop_invalid_status` → `drop_duplicate_ids` (only on loadable rows).
- `unify_customer_spellings` is the only **dataset-level** step: countries map onto a known list (`build_canonicalizer`), customers don't, so the canonical spelling is learned from the data — names equal under `fold` are one customer, written like most of their rows (`_spelling_rank` breaks ties: mixed case, then accents). The name is the customer's identity (no id column), so a variant left behind splits that customer's totals in `v_top_customers`. Its changes count in `text_values_normalized`; it never drops rows, so the invariant below is unaffected.
- Invariant (tested): `rows_out == rows_in - duplicates_removed - rows_dropped_bad_date - rows_dropped_bad_amount - rows_dropped_invalid_status`.
- Decisions: blank/invalid/negative amounts drop the row (never imputed); unknown countries are kept as written; unknown statuses dropped (DB CHECK would reject them); a customer seen in a single spelling is kept as written.
- Not validated yet (the generator never produces them; listed as future work in the README): blank `order_id`/`customer`/`country`/`product`, implausible years (`01/01/0025` parses), amounts with 3+ decimals or exponents (`1.234` → 1.23, `1e5` → 100000).
- Output types: `order_date` is `datetime.date`, `amount` is a float rounded to cents.

## Loading (`etl/load.py`)

`run_pipeline(csv, engine, steps)`:

1. `read_raw_csv`: `dtype=str, keep_default_na=False` — pandas must not guess types or turn blanks/"N/A" into NaN.
2. `clean_orders`.
3. `apply_sql_file("schema.sql")` — idempotent `CREATE ... IF NOT EXISTS`.
4. `load_orders`: `TRUNCATE` + `to_sql(if_exists="append", method="multi")` in **one transaction** (idempotent, all-or-nothing). Never use `if_exists="replace"`: it drops the table with its constraints, indexes and grants.
5. `apply_sql_file("views.sql")` — must run after the table exists.

`db.apply_sql_file` executes scripts through the raw psycopg cursor: SQLAlchemy `text()` would treat `::date` casts as bind parameters, and psycopg only accepts multiple statements when no parameters are passed.
