"""Question -> SQL -> validation -> execution, independent of any web framework.

`QueryService` composes three replaceable parts (an LLM SQL generator, a SQL
validator and a query runner). The FastAPI layer only translates HTTP to this
and errors back to HTTP, so the same service can back a CLI, a notebook or a
different API.
"""

import logging
from dataclasses import dataclass
from typing import Any

from orders_nl.db import QueryRunner
from orders_nl.errors import InvalidQueryError
from orders_nl.llm import SQLGenerator
from orders_nl.validator import SqlValidator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answer:
    """Result of answering a question, including the SQL that was actually run."""

    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]

    @property
    def row_count(self) -> int:
        return len(self.rows)


class QueryService:
    def __init__(self, generator: SQLGenerator, validator: SqlValidator, runner: QueryRunner) -> None:
        self._generator = generator
        self._validator = validator
        self._runner = runner

    def ask(self, question: str) -> Answer:
        """Answer a natural-language question.

        Raises (see orders_nl.errors):
            LLMError / UnanswerableQuestionError  the LLM produced no usable SQL
            InvalidQueryError                     the validator or the database rejected it
            QueryExecutionError                   the database failed to run it
        Nothing is executed unless the validator accepted the SQL.
        """
        raw_sql = self._generator.generate_sql(question)

        try:
            safe_sql = self._validator.validate(raw_sql)
        except InvalidQueryError as exc:
            logger.warning("Rejected generated SQL (%s): %s", exc.reason, raw_sql)
            exc.sql = raw_sql  # let the caller show what the LLM produced
            raise

        try:
            result = self._runner(safe_sql)
        except InvalidQueryError as exc:
            exc.sql = exc.sql or safe_sql
            raise

        return Answer(question=question, sql=safe_sql, columns=result.columns, rows=result.rows)
