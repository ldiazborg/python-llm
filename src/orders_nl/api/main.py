"""FastAPI app (requirement 3).

Run:  uv run uvicorn orders_nl.api.main:app --reload
Docs: http://localhost:8000/docs

The route only translates HTTP <-> `QueryService`; dependencies are plain
FastAPI providers so tests (or other deployments) override them via
`app.dependency_overrides`.

Dependency graph (FastAPI resolves it per request, from the bottom up):

    get_settings
      ├── get_sql_generator ──────────────┐
      ├── get_validator ──────────────────┤
      ├── get_query_runner ───────────────┼──> get_query_service ──> /ask
      └── get_answer_writer               │
            └── get_answer_composer ──────┘

Overriding any node replaces everything built from it, e.g. the tests swap
get_sql_generator / get_query_runner / get_answer_writer for fakes.
"""

import logging
from typing import Annotated

from fastapi import Depends, FastAPI

from orders_nl.answers import AnswerComposer
from orders_nl.api.errors import ERROR_MAP, register_error_handlers
from orders_nl.api.schemas import AskRequest, AskResponse, ErrorResponse
from orders_nl.config import ALLOWED_TABLES, DB_SCHEMA, Settings, get_settings
from orders_nl.db import QueryRunner, make_readonly_runner
from orders_nl.llm import AnswerWriter, OpenAICompatibleAnswerWriter, OpenAICompatibleSQLGenerator, SQLGenerator
from orders_nl.service import QueryService
from orders_nl.validator import QueryPolicy, SqlValidator

# --- dependency providers ------------------------------------------------------
# Built per request: they're cheap (no connection is opened until used) and it
# keeps settings changes and test overrides simple.


def get_sql_generator(settings: Annotated[Settings, Depends(get_settings)]) -> SQLGenerator:
    """LLM that turns the question into SQL (vLLM via the OpenAI API)."""
    return OpenAICompatibleSQLGenerator.from_settings(settings)


def get_query_runner(settings: Annotated[Settings, Depends(get_settings)]) -> QueryRunner:
    """Executes validated SQL as the read-only database role."""
    return make_readonly_runner(settings.reader_conninfo)


def get_validator(settings: Annotated[Settings, Depends(get_settings)]) -> SqlValidator:
    """SQL validator restricted to the allowed tables of the analytics schema."""
    policy = QueryPolicy(allowed_tables=ALLOWED_TABLES, schema=DB_SCHEMA, row_limit=settings.query_row_limit)
    return SqlValidator(policy)


def get_answer_writer(settings: Annotated[Settings, Depends(get_settings)]) -> AnswerWriter:
    """LLM that phrases the result as a Spanish sentence."""
    return OpenAICompatibleAnswerWriter.from_settings(settings)


def get_answer_composer(writer: Annotated[AnswerWriter, Depends(get_answer_writer)]) -> AnswerComposer:
    """Checks the writer's sentence against the data; falls back to templates."""
    return AnswerComposer(writer)


def get_query_service(
    generator: Annotated[SQLGenerator, Depends(get_sql_generator)],
    validator: Annotated[SqlValidator, Depends(get_validator)],
    runner: Annotated[QueryRunner, Depends(get_query_runner)],
    composer: Annotated[AnswerComposer, Depends(get_answer_composer)],
) -> QueryService:
    """The whole question -> answer pipeline, assembled from the providers above."""
    return QueryService(generator, validator, runner, composer)


# --- app -----------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Application factory: build a configured app (handy for tests and embedding)."""
    # title/version/description appear at the top of /docs.
    app = FastAPI(
        title="Orders NL Query API",
        version="0.1.0",
        description="Ask questions about orders in natural language; answered with read-only SQL.",
    )
    # AppError subclasses -> uniform JSON error bodies (see api/errors.py).
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
        """Natural-language question -> LLM SQL -> validation -> read-only execution
        -> (optional) natural-language answer.

        Sync endpoint on purpose: the LLM and DB clients are blocking, and FastAPI
        runs sync endpoints in a thread pool so they don't block the event loop.
        """
        # Errors raised here (LLMError, InvalidQueryError...) never reach the
        # client as tracebacks: the handlers from register_error_handlers render them.
        answer = service.ask(req.question, natural_answer=req.natural_answer)
        natural = answer.natural  # None when natural_answer=false
        return AskResponse(
            question=answer.question,
            answer=natural.text if natural else None,
            answer_source=natural.source if natural else None,
            sql=answer.sql,
            columns=answer.columns,
            rows=answer.rows,
            row_count=answer.row_count,
        )

    return app


# INFO level so the generated SQL and discarded LLM answers show up in the
# server console. basicConfig is a no-op if logging was already configured.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Module-level instance that uvicorn imports ("orders_nl.api.main:app").
app = create_app()
