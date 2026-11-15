"""ETL: generate the dirty CSV, clean it and load it into PostgreSQL.

Modules:
    generate.py  CLI `orders-generate` (thin wrapper over orders_nl.generation)
    parsers.py   pure per-value parsers: dates, amounts, text normalization
    clean.py     step-based cleaning pipeline + CleaningReport
    load.py      CLI `orders-load`: CSV -> clean -> table -> views
"""
