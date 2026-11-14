"""FastAPI app (requirement 3).

Run:  uv run uvicorn orders_nl.api.main:app --reload
Docs: http://localhost:8000/docs

The route only translates HTTP <-> `QueryService`; dependencies are plain
FastAPI providers so tests (or other deployments) override them via
`app.dependency_overrides`.
"""

import logging
from typing import Annotated

from fastapi import Depends, FastAPI

from orders_nl.api.errors import ERROR_MAP, register_error_handlers
from orders_nl.api.schemas import AskRequest, AskResponse, ErrorResponse
from orders_nl.config import ALLOWED_TABLES, DB_SCHEMA, Settings, get_settings
from orders_nl.db import QueryRunner, make_readonly_runner
from orders_nl.llm import OpenAICompatibleSQLGenerator, SQLGenerator
from orders_nl.service import QueryService
from orders_nl.validator import QueryPolicy, SqlValidator

# --- dependency providers ------------------------------------------------------
# Built per request: they're cheap (no connection is opened until used) and it
# keeps settings changes and test overrides simple.


def get_sql_generator(settings: Annotated[Settings, Depends(get_settings)]) -> SQLGenerator:
    return OpenAICompatibleSQLGenerator.from_settings(settings)


def get_query_runner(settings: Annotated[Settings, Depends(get_settings)]) -> QueryRunner:
    return make_readonly_runner(settings.reader_conninfo)


def get_validator(settings: Annotated[Settings, Depends(get_settings)]) -> SqlValidator:
    policy = QueryPolicy(allowed_tables=ALLOWED_TABLES, schema=DB_SCHEMA, row_limit=settings.query_row_limit)
    return SqlValidator(policy)


def get_query_service(
    generator: Annotated[SQLGenerator, Depends(get_sql_generator)],
    validator: Annotated[SqlValidator, Depends(get_validator)],
    runner: Annotated[QueryRunner, Depends(get_query_runner)],
) -> QueryService:
    return QueryService(generator, validator, runner)


# --- app -----------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Application factory: build a configured app (handy for tests and embedding)."""
    app = FastAPI(
        title="Orders NL Query API",
        version="0.1.0",
        description="Ask questions about orders in natural language; answered with read-only SQL.",
    )
    register_error_handlers(app)

    # Documented error responses, derived from the same map the handlers use.
    # Several codes can share a status (422), so group them per status.
    codes_by_status: dict[int, list[str]] = {}
    for status, code in ERROR_MAP.values():
        codes_by_status.setdefault(status, []).append(code)
    error_responses = {
        status: {"model": ErrorResponse, "description": " | ".join(codes)}
        for status, codes in sorted(codes_by_status.items())
    }

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe (does not call the LLM or the database)."""
        return {"status": "ok"}

    @app.post("/ask", response_model=AskResponse, responses=error_responses)  # type: ignore[arg-type]
    def ask(req: AskRequest, service: Annotated[QueryService, Depends(get_query_service)]) -> AskResponse:
        """Natural-language question -> LLM SQL -> validation -> read-only execution.

        Sync endpoint on purpose: the LLM and DB clients are blocking, and FastAPI
        runs sync endpoints in a thread pool so they don't block the event loop.
        """
        answer = service.ask(req.question)
        return AskResponse(
            question=answer.question,
            sql=answer.sql,
            columns=answer.columns,
            rows=answer.rows,
            row_count=answer.row_count,
        )

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
