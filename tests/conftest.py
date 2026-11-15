"""Shared fixtures: a simulated LLM, a fake query runner and an API test client."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from orders_nl.api.main import app, get_answer_writer, get_query_runner, get_sql_generator
from orders_nl.config import Settings, get_settings
from orders_nl.db import QueryResult


class FakeSQLGenerator:
    """Simulated LLM: returns a canned SQL string or raises a canned error."""

    def __init__(self, sql: str | None = None, error: Exception | None = None) -> None:
        self.sql = sql
        self.error = error
        self.questions: list[str] = []  # what the API asked, for assertions

    def generate_sql(self, question: str) -> str:
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        assert self.sql is not None, "set fake_llm.sql or fake_llm.error in the test"
        return self.sql


class FakeQueryRunner:
    """Records executed SQL and returns canned rows (or raises a canned error)."""

    def __init__(self, result: QueryResult | None = None, error: Exception | None = None) -> None:
        self.result = result or QueryResult(columns=[], rows=[])
        self.error = error
        self.executed: list[str] = []

    def __call__(self, sql: str) -> QueryResult:
        self.executed.append(sql)
        if self.error is not None:
            raise self.error
        return self.result


class FakeAnswerWriter:
    """Simulated answer-writing LLM: canned sentence or canned error."""

    def __init__(self, text: str = "Respuesta de prueba.", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str, QueryResult]] = []  # (question, sql, result)

    def write_answer(self, question: str, sql: str, result: QueryResult) -> str:
        self.calls.append((question, sql, result))
        if self.error is not None:
            raise self.error
        return self.text


@pytest.fixture
def fake_llm() -> FakeSQLGenerator:
    return FakeSQLGenerator(sql="SELECT * FROM orders")


@pytest.fixture
def fake_db() -> FakeQueryRunner:
    return FakeQueryRunner()


@pytest.fixture
def fake_writer() -> FakeAnswerWriter:
    return FakeAnswerWriter()


@pytest.fixture
def client(
    fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner, fake_writer: FakeAnswerWriter
) -> Iterator[TestClient]:
    """API client with both LLM calls and the database replaced by fakes (no network)."""
    # _env_file=None: tests never depend on the developer's local .env.
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    app.dependency_overrides[get_sql_generator] = lambda: fake_llm
    app.dependency_overrides[get_query_runner] = lambda: fake_db
    app.dependency_overrides[get_answer_writer] = lambda: fake_writer
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
