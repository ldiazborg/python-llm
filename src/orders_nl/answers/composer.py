"""Choose the answer text: the LLM's sentence when it is grounded, else a template."""

import logging
from dataclasses import dataclass
from typing import Literal

from orders_nl.answers.grounding import allowed_numbers, find_misattributed_numbers, find_unsupported_numbers
from orders_nl.answers.templates import template_answer
from orders_nl.db import QueryResult
from orders_nl.errors import LLMError
from orders_nl.llm import AnswerWriter

logger = logging.getLogger(__name__)

AnswerSource = Literal["llm", "template"]


@dataclass(frozen=True)
class NaturalAnswer:
    """The answer sentence and where it came from (shown to API clients)."""

    text: str
    source: AnswerSource


class AnswerComposer:
    """Produce a NaturalAnswer that never fails and never shows an invented number.

    writer=None means templates only (no extra LLM call).
    """

    def __init__(self, writer: AnswerWriter | None = None, *, max_length: int = 600) -> None:
        self._writer = writer
        self._max_length = max_length

    def compose(self, question: str, sql: str, result: QueryResult) -> NaturalAnswer:
        if self._writer is None:
            return self._template(result)

        try:
            text = self._writer.write_answer(question, sql, result).strip()
        except LLMError as exc:
            # The rows are already computed; a failed sentence must not fail the request.
            logger.warning("Answer writer failed, using template: %s", exc)
            return self._template(result)

        problem = self._problem_with(text, question, sql, result)
        if problem:
            logger.warning("Discarded LLM answer (%s): %r", problem, text)
            return self._template(result)
        return NaturalAnswer(text=text, source="llm")

    def _problem_with(self, text: str, question: str, sql: str, result: QueryResult) -> str | None:
        """Why the LLM sentence can't be used, or None if it is acceptable."""
        if not text:
            return "empty"
        if len(text) > self._max_length:
            return f"longer than {self._max_length} characters"
        allowed = allowed_numbers(result.rows, question, sql, row_count=len(result.rows))
        unsupported = find_unsupported_numbers(text, allowed)
        if unsupported:
            return f"numbers not in the data: {', '.join(unsupported)}"
        # Real numbers can still be paired with the wrong row (e.g. a reversed ranking).
        misattributed = find_misattributed_numbers(text, result.rows, question, sql)
        if misattributed:
            return f"numbers attributed to the wrong item: {', '.join(misattributed)}"
        return None

    @staticmethod
    def _template(result: QueryResult) -> NaturalAnswer:
        return NaturalAnswer(text=template_answer(result), source="template")
