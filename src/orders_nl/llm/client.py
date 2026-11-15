"""LLM client: question -> SQL through any OpenAI-compatible server (vLLM by default).

`SQLGenerator` is the only interface the rest of the app depends on. Swapping
providers means writing another class with `generate_sql`, or pointing
`LLM_BASE_URL` at another OpenAI-compatible server (Ollama, llama.cpp, ...).

`make_openai_client` and `chat_completion` are shared by every LLM call in the
app (SQL generation here, answer writing in `llm/answer.py`), so connection
settings and error handling live in one place.
"""

import logging
from typing import Protocol

import openai

from orders_nl.config import Settings
from orders_nl.errors import LLMError, UnanswerableQuestionError
from orders_nl.llm.prompt import CANNOT_ANSWER, build_messages, extract_sql

logger = logging.getLogger(__name__)


def make_openai_client(settings: Settings) -> openai.OpenAI:
    """OpenAI SDK client pointed at the configured server (vLLM by default)."""
    return openai.OpenAI(
        base_url=settings.llm_base_url,
        # The SDK requires a key; local servers without auth accept anything.
        api_key=settings.llm_api_key.get_secret_value() or "EMPTY",
        timeout=settings.llm_timeout_s,
        max_retries=1,
    )


def chat_completion(
    client: openai.OpenAI,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float = 0,
) -> str:
    """Run one chat completion and return its text, or raise LLMError.

    temperature=0 by default: same input, same output (reproducible answers).
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except openai.APIError as exc:
        # Covers connection errors, timeouts and non-2xx responses.
        logger.warning("LLM request failed: %r", exc)
        raise LLMError(f"The language model is unavailable ({exc.__class__.__name__})") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content or not content.strip():
        raise LLMError("The language model returned an empty response")
    return content.strip()


class SQLGenerator(Protocol):
    """Anything that turns a natural-language question into a SQL string.

    Must raise `LLMError` (or a subclass) when it can't produce SQL.
    """

    def generate_sql(self, question: str) -> str: ...


class OpenAICompatibleSQLGenerator:
    """SQLGenerator backed by the Chat Completions API.

    Takes a ready `openai.OpenAI` client (dependency injection), so tests can
    pass one with a mocked HTTP transport; use `from_settings` in the app.
    """

    def __init__(
        self,
        client: openai.OpenAI,
        model: str,
        *,
        max_tokens: int = 300,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatibleSQLGenerator":
        return cls(make_openai_client(settings), settings.llm_model)

    def generate_sql(self, question: str) -> str:
        content = chat_completion(
            self._client, self._model, build_messages(question), max_tokens=self._max_tokens
        )
        sql = extract_sql(content)
        if sql.upper() == CANNOT_ANSWER:
            raise UnanswerableQuestionError("The question can't be answered with the available sales data")
        logger.info("LLM generated SQL: %s", sql)
        return sql
