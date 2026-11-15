# Testing

Commands are in [commands.md](commands.md#tests). Unit tests need no Docker, no GPU and no `.env`.

## Layout

| File | Focus |
|---|---|
| `test_generate.py` | size, seed reproducibility, each defect really injected, composable defects |
| `test_parsers.py` | accepted/rejected date and amount formats, text folding |
| `test_clean.py` | hand-made dirty rows, customer spellings + generator→cleaner end-to-end invariants |
| `test_validator.py` | `VALID` / `INVALID` lists (52 attacks), LIMIT handling, CTE scoping, custom rules |
| `test_llm.py` | both LLM clients over a mocked HTTP transport, prompt contents, `extract_sql` |
| `test_answers.py` | number grounding/attribution (with real model outputs), templates, composer |
| `test_db.py` | runner helpers that need no database: JSON-friendly values, unique column names, psycopg error → app error |
| `test_config.py` | setting ranges rejected at load time, connection strings with special characters |
| `test_api.py` | endpoint with all collaborators faked: success, every error code, natural answers |
| `integration/test_postgres.py` | real load, idempotency, views, reader-role permissions with raw (unvalidated) SQL, timeout, repeated column names (opt-in) |

The brief's three required tests are cleaning (`test_clean.py`), query validation (`test_validator.py`) and the endpoint with a simulated LLM (`test_api.py`).

## Fakes and fixtures (`tests/conftest.py`)

- `FakeSQLGenerator` (`.sql` / `.error`, records `.questions`), `FakeQueryRunner` (`.result` / `.error`, records `.executed`), `FakeAnswerWriter` (`.text` / `.error`, records `.calls`).
- `client` fixture overrides `get_settings` (with `Settings(_env_file=None)`), `get_sql_generator`, `get_query_runner` and `get_answer_writer`, then clears the overrides.
- Assert on `fake_db.executed` to prove rejected SQL never reached the database.
- Test modules import helpers with `from conftest import ...` (works because `tests/` has no `__init__.py`; `tests/integration/` does).

## Mocking the OpenAI SDK

Build `openai.OpenAI(base_url=..., max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))` and inject it into the generator/writer; `handler(request)` returns `httpx.Response` or raises `httpx.ConnectError`. See `_generator` / `_answer_writer` in `test_llm.py`.

## Integration tests

Marked `integration` and skipped unless `RUN_INTEGRATION=1`. They need `docker compose up -d postgres` and a valid `.env`, and they TRUNCATE + reload `analytics.orders` with a 1000-row dataset — run `uv run orders-load` afterwards to restore the full data.

## Conventions

- When the real model misbehaves, add its exact output as a regression case (see `test_reversed_ranking_is_detected`, the `SELECT CURRENT_TIMESTAMP` cases).
- Every test has a one-line docstring stating the behaviour it protects.
