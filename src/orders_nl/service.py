"""Question -> SQL -> validation -> execution -> answer, independent of any web framework.

`QueryService` composes replaceable parts: an LLM SQL generator, a SQL
validator, a query runner and (optionally) an answer composer that phrases the
result in natural language. The FastAPI layer only translates HTTP to this and
errors back to HTTP, so the same service can back a CLI, a notebook or a
different API.
"""

import logging
from dataclasses import dataclass
from typing import Any

from orders_nl.answers import AnswerComposer, NaturalAnswer
from orders_nl.db import QueryRunner
from orders_nl.errors import InvalidQueryError
from orders_nl.llm import SQLGenerator
from orders_nl.validator import SqlValidator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answer:
    """Result of answering a question, including the SQL that was actually run.

    `natural` is the sentence answer; None when it wasn't requested or no
    composer is configured.
    """

    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    natural: NaturalAnswer | None = None

    @property
    def row_count(self) -> int:
        """Number of rows returned (derived, so it can never disagree with `rows`)."""
        return len(self.rows)


class QueryService:
    """Answers questions by orchestrating the LLM, the validator and the database.

    Every collaborator is injected, so each can be replaced independently
    (a fake LLM in tests, another database, a template-only composer, ...).

    Example:
        service = QueryService(generator, validator, runner, AnswerComposer(writer))
        answer = service.ask("¿Cuánto vendimos en España en marzo?")
        answer.sql, answer.rows, answer.natural.text
    """

    def __init__(
        self,
        generator: SQLGenerator,
        validator: SqlValidator,
        runner: QueryRunner,
        composer: AnswerComposer | None = None,
    ) -> None:
        """Create the service.

        Args:
            generator: question -> SQL (normally the LLM).
            validator: checks and normalizes that SQL before it runs.
            runner: executes validated SQL (normally read-only Postgres).
            composer: phrases the result; None = never produce `natural`.
        """
        self._generator = generator
        self._validator = validator
        self._runner = runner
        self._composer = composer

    def ask(self, question: str, *, natural_answer: bool = True) -> Answer:
        """Answer a natural-language question.

        Raises (see orders_nl.errors):
            LLMError / UnanswerableQuestionError  the LLM produced no usable SQL
            InvalidQueryError                     the validator or the database rejected it
            QueryExecutionError                   the database failed to run it
        Nothing is executed unless the validator accepted the SQL. Phrasing the
        answer never raises: the composer falls back to a template.

        Args:
            question: natural-language question.
            natural_answer: also phrase the result as a sentence (an extra LLM call).
        """
        # 1. Question -> SQL. LLM errors propagate unchanged.
        raw_sql = self._generator.generate_sql(question)

        # 2. Validate. Only `safe_sql` (normalized, LIMIT applied) is ever executed.
        try:
            safe_sql = self._validator.validate(raw_sql)
        except InvalidQueryError as exc:
            logger.warning("Rejected generated SQL (%s): %s", exc.reason, raw_sql)
            exc.sql = raw_sql  # let the caller show what the LLM produced
            raise

        # 3. Execute. The runner raises InvalidQueryError when Postgres rejects the
        #    SQL (e.g. unknown column); attach the SQL that was run.
        try:
            result = self._runner(safe_sql)
        except InvalidQueryError as exc:
            exc.sql = exc.sql or safe_sql
            raise

        # 4. Optionally phrase the result. compose() never raises LLMError.
        natural = None
        if natural_answer and self._composer is not None:
            natural = self._composer.compose(question, safe_sql, result)

        return Answer(
            question=question,
            sql=safe_sql,
            columns=result.columns,
            rows=result.rows,
            natural=natural,
        )
