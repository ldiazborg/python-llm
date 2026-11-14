"""FastAPI app. Run: uv run uvicorn orders_nl.api.main:app --reload"""

from typing import Annotated

from fastapi import Depends, FastAPI

from orders_nl.api.schemas import AskRequest, AskResponse, ErrorResponse
from orders_nl.config import ALLOWED_TABLES, Settings, get_settings
from orders_nl.db import QueryRunner, make_readonly_runner
from orders_nl.llm import OpenAICompatibleSQLGenerator, SQLGenerator

app = FastAPI(title="Orders NL Query API", version="0.1.0")


# --- Dependencies (overridden in tests via app.dependency_overrides) ---------


def get_sql_generator(settings: Annotated[Settings, Depends(get_settings)]) -> SQLGenerator:
    return OpenAICompatibleSQLGenerator(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )


def get_query_runner(settings: Annotated[Settings, Depends(get_settings)]) -> QueryRunner:
    return make_readonly_runner(settings.reader_conninfo)


# --- Routes ------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/ask",
    response_model=AskResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Generated SQL rejected by the validator"},
        502: {"model": ErrorResponse, "description": "LLM unavailable or returned nothing usable"},
        500: {"model": ErrorResponse, "description": "Query failed in the database"},
    },
)
def ask(
    req: AskRequest,
    llm: Annotated[SQLGenerator, Depends(get_sql_generator)],
    run_query: Annotated[QueryRunner, Depends(get_query_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AskResponse:
    """Natural-language question -> LLM SQL -> validate -> execute read-only -> rows.

    TODO: implement the pipeline and map failures to ErrorResponse bodies:
      1. sql = llm.generate_sql(req.question)
           LLMError             -> 502 {"error": "llm_unavailable", ...}
      2. safe_sql = validate_sql(sql, ALLOWED_TABLES, row_limit=settings.query_row_limit)
           InvalidQueryError    -> 422 {"error": "invalid_query", "detail": reason, "sql": sql}
      3. rows = run_query(safe_sql)
           psycopg.Error        -> 500 {"error": "query_failed", ...}  (don't leak internals)
      4. return AskResponse(question=..., sql=safe_sql, columns=..., rows=rows, row_count=...)
    Tip: return JSONResponse(status_code=..., content=ErrorResponse(...).model_dump())
    so the body matches the documented ErrorResponse instead of FastAPI's {"detail": ...}.
    """
    _ = (llm, run_query, settings, ALLOWED_TABLES)
    raise NotImplementedError("TODO: implement /ask")
