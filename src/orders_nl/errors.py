"""Application error hierarchy.

Every expected failure of the question -> answer pipeline is one of these, so
callers (the API, a CLI, tests) can handle them uniformly. The API maps each
type to an HTTP status and error code in `orders_nl.api.errors`.

    AppError
    ├── LLMError                    LLM unreachable / empty / unusable output
    │   └── UnanswerableQuestionError   LLM says the data can't answer it
    ├── InvalidQueryError           SQL rejected (validator or database)
    └── QueryExecutionError         query failed for an operational reason
        └── QueryTimeoutError       exceeded the statement timeout

Messages (`str(exc)`) are shown to API clients as the `detail` field, so they
must be human-readable and must not contain internal details (stack traces,
connection strings, server paths).
"""


class AppError(Exception):
    """Base class for all expected application errors.

    Catching `AppError` handles every anticipated failure while still letting
    genuine bugs (TypeError, KeyError, ...) surface as crashes.
    """


class LLMError(AppError):
    """The LLM could not be reached or returned nothing usable.

    Raised for connection errors, timeouts, non-2xx responses and empty
    completions. HTTP 502: the upstream model service failed, not the client.
    """


class UnanswerableQuestionError(LLMError):
    """The LLM determined the question can't be answered with the available tables.

    The model signals this by replying with the CANNOT_ANSWER sentinel (see
    llm/prompt.py). A subclass of LLMError so generic handlers still work, but
    mapped to its own 422 code because it is the question, not the service,
    that is the problem.
    """


class InvalidQueryError(AppError):
    """The SQL is not a single, read-only query over the allowed tables.

    Raised by the validator (before execution) and by the query runner when
    PostgreSQL rejects the SQL (e.g. a column that doesn't exist).

    Attributes:
        reason: human-readable explanation (also the exception message).
        sql: the rejected statement when known, so API responses can show
            what the LLM produced. Filled in by the service layer.
    """

    def __init__(self, reason: str, *, sql: str | None = None) -> None:
        """Create the error.

        Args:
            reason: why the query was rejected; shown to the client.
            sql: the rejected SQL, if already known (keyword-only).
        """
        super().__init__(reason)
        self.reason = reason
        self.sql = sql


class QueryExecutionError(AppError):
    """A validated query could not be executed (connection, permissions, ...)."""


class QueryTimeoutError(QueryExecutionError):
    """The query was cancelled by the database statement timeout (5 s, set on the role)."""
