"""Turn query results into a natural-language answer that can be trusted.

    AnswerComposer.compose(question, sql, result) -> NaturalAnswer(text, source)

source == "llm":      the LLM's sentence; every number in it is backed by the data and
                      attributed to the right row
source == "template": deterministic Spanish text, used when there is no LLM writer,
                      it failed, or its sentence contained an unsupported number

Modules:
    grounding.py  find numbers in text, check they exist in the data and sit
                  next to the right row (pure)
    templates.py  deterministic Spanish answers and number formatting (pure)
    composer.py   chooses between the LLM sentence and the template
"""

from orders_nl.answers.composer import AnswerComposer, AnswerSource, NaturalAnswer
from orders_nl.answers.grounding import find_misattributed_numbers, find_unsupported_numbers
from orders_nl.answers.templates import template_answer

__all__ = [
    "AnswerComposer",
    "AnswerSource",
    "NaturalAnswer",
    "find_misattributed_numbers",
    "find_unsupported_numbers",
    "template_answer",
]
