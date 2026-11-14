"""Endpoint tests with a simulated LLM and a fake DB (requirement 4)."""

from fastapi.testclient import TestClient

from conftest import FakeQueryRunner, FakeSQLGenerator, todo
from orders_nl.llm import LLMError


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_ask_validates_question_length(client: TestClient) -> None:
    assert client.post("/ask", json={"question": ""}).status_code == 422


@todo
def test_ask_returns_rows(client: TestClient, fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner) -> None:
    fake_llm.sql = (
        "SELECT SUM(amount) AS total FROM orders "
        "WHERE country = 'España' AND EXTRACT(MONTH FROM order_date) = 3"
    )
    fake_db.rows = [{"total": 12345.67}]

    resp = client.post("/ask", json={"question": "¿Cuánto vendimos en España en marzo?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == [{"total": 12345.67}]
    assert body["columns"] == ["total"]
    assert body["row_count"] == 1
    assert fake_db.executed == [body["sql"]]  # runs the validated SQL, not the raw one
    assert "LIMIT" in body["sql"].upper()


@todo
def test_ask_llm_failure_returns_clear_error(
    client: TestClient, fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner
) -> None:
    fake_llm.error = LLMError("connection refused")

    resp = client.post("/ask", json={"question": "¿Cuánto vendimos?"})

    assert resp.status_code == 502
    assert resp.json()["error"] == "llm_unavailable"
    assert fake_db.executed == []


@todo
def test_ask_rejects_unsafe_sql(
    client: TestClient, fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner
) -> None:
    fake_llm.sql = "DROP TABLE orders"

    resp = client.post("/ask", json={"question": "borra todo"})

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_query"
    assert body["sql"] == "DROP TABLE orders"
    assert fake_db.executed == []
