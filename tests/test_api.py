"""Endpoint tests with a simulated LLM and a fake database (requirement 4)."""

import pytest
from fastapi.testclient import TestClient

from conftest import FakeQueryRunner, FakeSQLGenerator
from orders_nl.db import QueryResult
from orders_nl.errors import (
    InvalidQueryError,
    LLMError,
    QueryExecutionError,
    QueryTimeoutError,
    UnanswerableQuestionError,
)

QUESTION = "¿Cuánto vendimos en España en marzo?"


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("question", ["", "ab", "x" * 501])
def test_ask_validates_question_length(client: TestClient, question: str) -> None:
    assert client.post("/ask", json={"question": question}).status_code == 422


def test_ask_returns_rows(client: TestClient, fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner) -> None:
    fake_llm.sql = (
        "SELECT SUM(total_amount) AS total_sales FROM v_sales_by_month_country "
        "WHERE country = 'España' AND month = 3"
    )
    fake_db.result = QueryResult(columns=["total_sales"], rows=[{"total_sales": 12345.67}])

    resp = client.post("/ask", json={"question": QUESTION})

    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == QUESTION
    assert body["rows"] == [{"total_sales": 12345.67}]
    assert body["columns"] == ["total_sales"]
    assert body["row_count"] == 1
    assert fake_llm.questions == [QUESTION]
    assert fake_db.executed == [body["sql"]]  # runs the validated SQL, not the raw one
    assert "LIMIT 1000" in body["sql"].upper()


def test_ask_llm_failure_returns_clear_error(
    client: TestClient, fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner
) -> None:
    fake_llm.error = LLMError("The language model is unavailable (APIConnectionError)")

    resp = client.post("/ask", json={"question": QUESTION})

    assert resp.status_code == 502
    assert resp.json() == {
        "error": "llm_unavailable",
        "detail": "The language model is unavailable (APIConnectionError)",
        "sql": None,
    }
    assert fake_db.executed == []


def test_ask_unanswerable_question(
    client: TestClient, fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner
) -> None:
    fake_llm.error = UnanswerableQuestionError("The question can't be answered")

    resp = client.post("/ask", json={"question": "¿Qué tiempo hace?"})

    assert resp.status_code == 422
    assert resp.json()["error"] == "unanswerable_question"
    assert fake_db.executed == []


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DROP TABLE orders",
        "SELECT * FROM pg_shadow",
        "SELECT 1; DELETE FROM orders",
        "Lo siento, no puedo ayudar con eso.",  # model ignored the instructions
    ],
)
def test_ask_rejects_invalid_sql_without_running_it(
    client: TestClient, fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner, bad_sql: str
) -> None:
    fake_llm.sql = bad_sql

    resp = client.post("/ask", json={"question": "borra todo"})

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_query"
    assert body["sql"] == bad_sql
    assert body["detail"]
    assert fake_db.executed == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidQueryError('The database rejected the query: column "x" does not exist'), 422, "invalid_query"),
        (QueryTimeoutError("The query took too long and was cancelled"), 504, "query_timeout"),
        (QueryExecutionError("The query could not be executed"), 500, "query_failed"),
    ],
)
def test_ask_database_errors(
    client: TestClient, fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner, error: Exception, status: int, code: str
) -> None:
    fake_llm.sql = "SELECT x FROM orders"
    fake_db.error = error

    resp = client.post("/ask", json={"question": QUESTION})

    assert resp.status_code == status
    body = resp.json()
    assert body["error"] == code
    assert body["detail"] == str(error)
    if code == "invalid_query":
        assert body["sql"] == fake_db.executed[0]  # the SQL the database rejected
