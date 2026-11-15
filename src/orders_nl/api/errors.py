"""Map application errors to HTTP responses with a uniform `ErrorResponse` body.

To support a new error: add the exception to `orders_nl.errors` and one entry
to `ERROR_MAP`. Starlette picks the handler of the most specific class, so a
subclass entry (UnanswerableQuestionError) overrides its parent (LLMError).

Every error body has the same shape, so clients can handle all failures with
one code path:

    {"error": "invalid_query", "detail": "Only SELECT queries are allowed (got DELETE)",
     "sql": "DELETE FROM orders"}

Unexpected exceptions (bugs) are not in the map: FastAPI turns them into a
plain 500 without leaking the traceback to the client.
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
#   502 Bad Gateway:          an upstream service (the LLM) failed
#   422 Unprocessable Entity: the question/SQL can't be processed as asked
#   500 Internal Server Error: the database failed for an operational reason
#   504 Gateway Timeout:      the query exceeded the statement timeout
ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    LLMError: (502, "llm_unavailable"),
    UnanswerableQuestionError: (422, "unanswerable_question"),
    InvalidQueryError: (422, "invalid_query"),
    QueryExecutionError: (500, "query_failed"),
    QueryTimeoutError: (504, "query_timeout"),
}


def _make_handler(status: int, code: str):
    """Build an exception handler that answers with `status` and error `code`.

    A factory (closure) so each ERROR_MAP entry gets its own handler with its
    own status/code baked in.
    """

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        """Render `exc` as an ErrorResponse JSON body."""
        # str(exc) is the human-readable message; only InvalidQueryError has `.sql`.
        body = ErrorResponse(error=code, detail=str(exc), sql=getattr(exc, "sql", None))
        return JSONResponse(status_code=status, content=body.model_dump())

    return handler


def register_error_handlers(app: FastAPI) -> None:
    """Install one handler per ERROR_MAP entry on `app`."""
    for exc_type, (status, code) in ERROR_MAP.items():
        app.add_exception_handler(exc_type, _make_handler(status, code))
