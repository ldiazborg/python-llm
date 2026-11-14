"""LLM client: any OpenAI-compatible server (vLLM in docker by default)."""

from typing import Protocol

import openai

from orders_nl.llm.prompt import CANNOT_ANSWER, build_messages, extract_sql


class LLMError(Exception):
    """The LLM could not be reached or returned nothing usable."""


class SQLGenerator(Protocol):
    def generate_sql(self, question: str) -> str: ...


class OpenAICompatibleSQLGenerator:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_s: float) -> None:
        self._model = model
        self._client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "EMPTY",
            timeout=timeout_s,
            max_retries=1,
        )

    def generate_sql(self, question: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=build_messages(question),  # type: ignore[arg-type]
                temperature=0,
                max_tokens=400,
            )
        except openai.APIError as exc:
            raise LLMError(f"LLM request failed: {exc.__class__.__name__}") from exc

        content = response.choices[0].message.content if response.choices else None
        if not content or not content.strip():
            raise LLMError("LLM returned an empty response")

        sql = extract_sql(content)
        if sql.upper() == CANNOT_ANSWER:
            raise LLMError("The question cannot be answered with the available data")
        return sql
