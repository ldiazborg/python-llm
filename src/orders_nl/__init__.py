"""Orders dataset + natural-language query API.

Package layout (data flows top to bottom):

    domain.py      shared vocabulary: columns, statuses, countries
    config.py      settings from .env; allowed tables
    generation/    synthetic dirty orders (Faker + defect injectors)
    etl/           CLIs: generate the CSV, clean it, load it into PostgreSQL
    sql/           table and views (the "models")
    db.py          admin engine (ETL) and read-only query runner (API)
    validator.py   AST-based SQL validation
    llm/           prompts and OpenAI-compatible clients (SQL + answer writing)
    answers/       trustworthy natural-language answers (checks + templates)
    service.py     question -> SQL -> validate -> run -> answer
    errors.py      AppError hierarchy
    api/           FastAPI app
"""
