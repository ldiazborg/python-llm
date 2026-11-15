# HTTP API (`api/`)

## Endpoints

| Route | Notes |
|---|---|
| `GET /health` | liveness only; touches neither LLM nor DB |
| `POST /ask` | body `AskRequest{question: 3..500 chars after stripping whitespace, natural_answer: bool = true}` → `AskResponse{question, answer, answer_source, sql, columns, rows, row_count}` |
| `GET /docs` | OpenAPI UI; route docstrings and pydantic `Field` descriptions are shown here, so keep them user-facing |

`sql` in the response is the **validated** SQL that was executed (normalized, with LIMIT), not the raw LLM output. With `natural_answer=false` the answer writer is never called and `answer`/`answer_source` are `null`.

## Errors (`api/errors.py`)

`ERROR_MAP` drives both the exception handlers and the documented OpenAPI responses:

| Exception (`errors.py`) | HTTP | `error` code |
|---|---|---|
| `LLMError` | 502 | `llm_unavailable` |
| `UnanswerableQuestionError` (subclass of `LLMError`) | 422 | `unanswerable_question` |
| `InvalidQueryError` | 422 | `invalid_query` (body includes the rejected `sql`) |
| `QueryExecutionError` | 500 | `query_failed` (generic message, no internals) |
| `QueryTimeoutError` (subclass of `QueryExecutionError`) | 504 | `query_timeout` |

Starlette picks the handler of the most specific class in the MRO, so subclass entries override their parents. Body shape: `{"error", "detail", "sql"}`. FastAPI's own request-validation 422 (e.g. question too short) keeps FastAPI's default `{"detail": [...]}` shape. `str(exc)` becomes `detail`, so exception messages must be safe to show to clients.

To add an error: subclass in `errors.py`, add one `ERROR_MAP` entry.

## Wiring (`api/main.py`)

- `create_app()` factory; module-level `app = create_app()` is what uvicorn imports.
- Dependency providers (built per request, cheap): `get_settings` → `get_sql_generator`, `get_validator`, `get_query_runner`, `get_answer_writer` → `get_answer_composer` → `get_query_service`. Override any of them via `app.dependency_overrides`.
- Endpoints are **sync on purpose**: the LLM and DB clients are blocking, FastAPI runs sync endpoints in a thread pool.
- `logging.basicConfig(level=INFO)` at import: generated SQL and discarded answers appear in the uvicorn console.
