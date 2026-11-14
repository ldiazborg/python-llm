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
"""


class AppError(Exception):
    """Base class for all expected application errors."""


class LLMError(AppError):
    """The LLM could not be reached or returned nothing usable."""


class UnanswerableQuestionError(LLMError):
    """The LLM determined the question can't be answered with the available tables."""


class InvalidQueryError(AppError):
    """The SQL is not a single, read-only query over the allowed tables.

    `sql` holds the rejected statement when known, so API responses can show
    what the LLM produced.
    """

    def __init__(self, reason: str, *, sql: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.sql = sql


class QueryExecutionError(AppError):
    """A validated query could not be executed (connection, permissions, ...)."""


class QueryTimeoutError(QueryExecutionError):
    """The query was cancelled by the database statement timeout."""
