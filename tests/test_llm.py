"""LLM client and prompt tests, with the OpenAI HTTP layer mocked (no server needed)."""

import json

import httpx
import openai
import pytest

from orders_nl.errors import LLMError, UnanswerableQuestionError
from orders_nl.llm import OpenAICompatibleSQLGenerator
from orders_nl.llm.prompt import CANNOT_ANSWER, FEW_SHOT_EXAMPLES, build_messages, extract_sql


def _generator(handler) -> OpenAICompatibleSQLGenerator:
    """Generator whose HTTP calls go to `handler(request) -> httpx.Response`."""
    client = openai.OpenAI(
        base_url="http://llm.test/v1",
        api_key="test",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OpenAICompatibleSQLGenerator(client, "test-model")


def _completion(content: str | None) -> httpx.Response:
    """Minimal Chat Completions response body."""
    return httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
            ],
        },
    )


def test_returns_sql_and_sends_prompt() -> None:
    """The SQL is extracted from fences and the request carries model, temperature and prompt."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the request body and reply with fenced SQL."""
        sent.update(json.loads(request.content))
        return _completion("```sql\nSELECT 1;\n```")

    assert _generator(handler).generate_sql("¿Cuánto vendimos?") == "SELECT 1"
    assert sent["model"] == "test-model"
    assert sent["temperature"] == 0
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][-1] == {"role": "user", "content": "¿Cuánto vendimos?"}


def test_connection_error_becomes_llm_error() -> None:
    """A network failure becomes LLMError ('unavailable')."""
    def handler(request: httpx.Request) -> httpx.Response:
        """Simulate the server refusing the connection."""
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="unavailable"):
        _generator(handler).generate_sql("q")


def test_server_error_becomes_llm_error() -> None:
    """An HTTP 500 from the server becomes LLMError."""
    with pytest.raises(LLMError):
        _generator(lambda r: httpx.Response(500, json={"error": "boom"})).generate_sql("q")


@pytest.mark.parametrize("content", [None, "", "   "])
def test_empty_reply_becomes_llm_error(content: str | None) -> None:
    """A null, empty or blank completion becomes LLMError ('empty')."""
    with pytest.raises(LLMError, match="empty"):
        _generator(lambda r: _completion(content)).generate_sql("q")


def test_cannot_answer_is_a_distinct_error() -> None:
    """The CANNOT_ANSWER sentinel raises UnanswerableQuestionError."""
    with pytest.raises(UnanswerableQuestionError):
        _generator(lambda r: _completion(CANNOT_ANSWER)).generate_sql("¿Qué tiempo hace?")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("SELECT 1", "SELECT 1"),
        ("  SELECT 1 ;  ", "SELECT 1"),
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("Here you go:\n```\nSELECT 1;\n```\nHope it helps", "SELECT 1"),
    ],
)
def test_extract_sql(reply: str, expected: str) -> None:
    """SQL is extracted from plain, padded, fenced and chatty replies."""
    assert extract_sql(reply) == expected


def test_prompt_contains_schema_and_examples() -> None:
    """The system prompt names every table and the message list includes all examples."""
    messages = build_messages("q")
    system = messages[0]["content"]
    for name in ("orders", "v_sales_by_month_country", "v_top_customers", "'España'", "'completed'"):
        assert name in system
    # system + one user/assistant pair per example + the question
    assert len(messages) == 1 + 2 * len(FEW_SHOT_EXAMPLES) + 1


# --- answer writer ---------------------------------------------------------------------

from orders_nl.db import QueryResult  # noqa: E402
from orders_nl.llm import OpenAICompatibleAnswerWriter  # noqa: E402
from orders_nl.llm.answer import build_answer_messages  # noqa: E402


def _answer_writer(handler, max_rows: int = 20) -> OpenAICompatibleAnswerWriter:
    """Answer writer whose HTTP calls go to `handler` (no server needed)."""
    client = openai.OpenAI(
        base_url="http://llm.test/v1",
        api_key="test",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OpenAICompatibleAnswerWriter(client, "test-model", max_rows=max_rows)


def test_answer_writer_sends_question_sql_and_rows() -> None:
    """The prompt includes question, SQL and rows; the reply is stripped."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the request body and reply with a padded sentence."""
        sent.update(json.loads(request.content))
        return _completion("  En marzo se vendieron 6.215,32 €.  ")

    result = QueryResult(columns=["total_sales"], rows=[{"total_sales": 6215.32}])
    text = _answer_writer(handler).write_answer("¿Cuánto en marzo?", "SELECT 1 FROM orders", result)

    assert text == "En marzo se vendieron 6.215,32 €."
    last = sent["messages"][-1]["content"]
    assert "¿Cuánto en marzo?" in last
    assert "SELECT 1 FROM orders" in last
    assert '"total_sales": 6215.32' in last


def test_answer_messages_cap_rows_and_say_so() -> None:
    """Only `max_rows` rows are sent, and the prompt says how many exist."""
    result = QueryResult(columns=["n"], rows=[{"n": i} for i in range(50)])
    last = build_answer_messages("q", "SELECT n FROM orders", result, max_rows=3)[-1]["content"]
    assert "Filas (3 de 50)" in last
    assert '{"n": 3}' not in last


def test_answer_writer_failure_is_llm_error() -> None:
    """An HTTP 503 from the server becomes LLMError."""
    with pytest.raises(LLMError):
        _answer_writer(lambda r: httpx.Response(503)).write_answer("q", "SELECT 1", QueryResult([], []))
