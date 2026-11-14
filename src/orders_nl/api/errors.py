"""Map application errors to HTTP responses with a uniform `ErrorResponse` body.

To support a new error: add the exception to `orders_nl.errors` and one entry
to `ERROR_MAP`. Starlette picks the handler of the most specific class, so a
subclass entry (UnanswerableQuestionError) overrides its parent (LLMError).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orders_nl.api.schemas import ErrorResponse
from orders_nl.errors import (
    InvalidQueryError,
    LLMError,
    QueryExecutionError,
    QueryTimeoutError,
    UnanswerableQuestionError,
)

# exception type -> (HTTP status, machine-readable error code)
ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    LLMError: (502, "llm_unavailable"),
    UnanswerableQuestionError: (422, "unanswerable_question"),
    InvalidQueryError: (422, "invalid_query"),
    QueryExecutionError: (500, "query_failed"),
    QueryTimeoutError: (504, "query_timeout"),
}


def _make_handler(status: int, code: str):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        body = ErrorResponse(error=code, detail=str(exc), sql=getattr(exc, "sql", None))
        return JSONResponse(status_code=status, content=body.model_dump())

    return handler


def register_error_handlers(app: FastAPI) -> None:
    for exc_type, (status, code) in ERROR_MAP.items():
        app.add_exception_handler(exc_type, _make_handler(status, code))
