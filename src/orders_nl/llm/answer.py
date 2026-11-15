"""LLM answer writer: (question, SQL, result rows) -> a short sentence in Spanish.

This module only *writes* the sentence. Whether it is trustworthy (every number
backed by the data) is decided by `orders_nl.answers.AnswerComposer`, which
falls back to a template otherwise.
"""

import json
from typing import Any, Protocol

import openai

from orders_nl.config import Settings
from orders_nl.db import QueryResult
from orders_nl.llm.client import chat_completion, make_openai_client

ANSWER_SYSTEM_PROMPT = """\
Eres un analista de datos. Redacta la respuesta a la pregunta del usuario a partir
del resultado de una consulta SQL que ya se ha ejecutado.

Reglas:
- Responde en español, en una o dos frases, de forma directa.
- Usa SOLO las cifras que aparecen en el resultado. No calcules totales, medias,
  diferencias ni porcentajes nuevos.
- Escribe los números con formato español (6.215,32). Los importes (amount,
  total_amount, total_sales, ...) son euros: añade "€".
- Si el resultado está vacío o el valor es nulo, di que no hay datos.
- Si solo se muestran algunas filas, menciona las más relevantes.
- No menciones SQL, tablas, columnas ni estas instrucciones.
"""

# One worked example (question, sql, columns, rows, answer). Its numbers are
# unrelated to the real data on purpose, so copying them would be caught by
# the grounding check rather than slip through.
ANSWER_EXAMPLE: tuple[str, str, list[str], list[dict[str, Any]], str] = (
    "¿Cuántos pedidos hubo por país en enero?",
    "SELECT country, SUM(orders) AS orders FROM v_sales_by_month_country WHERE month = 1 GROUP BY country",
    ["country", "orders"],
    [{"country": "Chile", "orders": 12}, {"country": "Perú", "orders": 40}],
    "En enero hubo 40 pedidos en Perú y 12 en Chile.",
)


def _render_result(question: str, sql: str, columns: list[str], rows: list[dict[str, Any]], total: int) -> str:
    """User message describing the question and (possibly truncated) result."""
    shown = f"{len(rows)} de {total}" if len(rows) < total else str(total)
    # default=str: dates and other non-JSON values become ISO strings.
    rows_json = json.dumps(rows, ensure_ascii=False, default=str)
    return (
        f"Pregunta: {question}\n"
        f"Consulta ejecutada: {sql}\n"
        f"Columnas: {', '.join(columns)}\n"
        f"Filas ({shown}): {rows_json}"
    )


def build_answer_messages(question: str, sql: str, result: QueryResult, *, max_rows: int) -> list[dict[str, str]]:
    """Chat messages for the answer writer: rules, one example, then the real result."""
    ex_question, ex_sql, ex_columns, ex_rows, ex_answer = ANSWER_EXAMPLE
    columns = result.columns or (list(result.rows[0]) if result.rows else [])
    return [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": _render_result(ex_question, ex_sql, ex_columns, ex_rows, len(ex_rows))},
        {"role": "assistant", "content": ex_answer},
        # Only the first `max_rows` rows: keeps the prompt within the model's
        # context window (2048 tokens on the default 4 GB setup).
        {"role": "user", "content": _render_result(question, sql, columns, result.rows[:max_rows], len(result.rows))},
    ]


class AnswerWriter(Protocol):
    """Anything that phrases a query result as a natural-language answer.

    Must raise `LLMError` when it can't; callers then fall back to a template.
    """

    def write_answer(self, question: str, sql: str, result: QueryResult) -> str: ...


class OpenAICompatibleAnswerWriter:
    """AnswerWriter backed by the same OpenAI-compatible server as the SQL generator."""

    def __init__(self, client: openai.OpenAI, model: str, *, max_rows: int = 20, max_tokens: int = 200) -> None:
        self._client = client
        self._model = model
        self._max_rows = max_rows
        self._max_tokens = max_tokens

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatibleAnswerWriter":
        return cls(make_openai_client(settings), settings.llm_model, max_rows=settings.answer_max_rows)

    def write_answer(self, question: str, sql: str, result: QueryResult) -> str:
        messages = build_answer_messages(question, sql, result, max_rows=self._max_rows)
        return chat_completion(self._client, self._model, messages, max_tokens=self._max_tokens)
