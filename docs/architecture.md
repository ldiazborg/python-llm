# Architecture

Two independent flows share one PostgreSQL database.

## ETL flow (CLIs, admin DB user)

```
orders-generate: generation/ (Faker + defects) ──► data/orders_raw.csv
orders-load:     read CSV as text ──► etl/clean.py ──► sql/schema.sql ──► TRUNCATE+INSERT ──► sql/views.sql
```

Details: [data-pipeline.md](data-pipeline.md), [database.md](database.md).

## Question flow (`POST /ask`, read-only DB role)

`api/main.py` → `service.QueryService.ask()`:

1. `SQLGenerator.generate_sql(question)` — LLM writes SQL ([llm.md](llm.md))
2. `SqlValidator.validate(raw_sql)` — AST rules, returns normalized SQL with LIMIT ([sql-validation.md](sql-validation.md))
3. `QueryRunner(safe_sql)` — executes **only** the validated SQL as the read-only role ([database.md](database.md))
4. `AnswerComposer.compose(...)` — optional sentence: LLM text if its numbers check out, else template ([natural-answers.md](natural-answers.md))

Every failure is an `AppError` subclass (`errors.py`) that `api/errors.py` maps to an HTTP status ([api.md](api.md)).

## Layering rules

- `domain.py`, `config.py`, `errors.py` are the bottom layer: everything may import them, and they import no other app module.
- `service.py` must not import FastAPI. The API layer only translates HTTP ⇄ service and exceptions ⇄ JSON.
- `db.py` is the only module that knows psycopg errors; it translates them into `AppError`s so the service and API never see driver exceptions.
- `answers/` depends on `llm/` (the `AnswerWriter` protocol) and `db.QueryResult`; `llm/` must not import `answers/`.

## Dependency injection

Collaborators are protocols/callables injected into `QueryService`:

| Seam | Type | Production implementation | Test fake (tests/conftest.py) |
|---|---|---|---|
| SQL generation | `llm.SQLGenerator` (protocol) | `OpenAICompatibleSQLGenerator` | `FakeSQLGenerator` |
| Query execution | `db.QueryRunner` = `Callable[[str], QueryResult]` | `make_readonly_runner(conninfo)` | `FakeQueryRunner` |
| Answer writing | `llm.AnswerWriter` (protocol) | `OpenAICompatibleAnswerWriter` | `FakeAnswerWriter` |

In the API these are FastAPI dependency providers (`get_sql_generator`, `get_query_runner`, `get_answer_writer`, ...) overridden in tests via `app.dependency_overrides`.

## Extension pattern

Behaviour is assembled from ordered lists of small functions/objects, each with a default tuple:

| What | Default list | Item signature |
|---|---|---|
| Data defects | `generation.default_defects()` | object with `name`, `apply(df, rng) -> df` |
| Cleaning | `etl.clean.DEFAULT_STEPS` | `(df, CleaningReport) -> df` |
| SQL validation | `validator.DEFAULT_RULES` | `(tree, QueryPolicy) -> None`, raises `InvalidQueryError` |
| API errors | `api.errors.ERROR_MAP` | `{ExceptionType: (status, code)}` |

Order matters in each list; the reasons are documented in the respective docs.
