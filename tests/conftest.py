from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orders_nl.api.main import app, get_query_runner, get_sql_generator
from orders_nl.config import Settings, get_settings
from orders_nl.llm import LLMError

# Marker for tests whose target is still a stub. strict=True: once the stub is
# implemented the test XPASSes -> fails, reminding you to remove the marker.
todo = pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="stub not implemented")


class FakeSQLGenerator:
    """Simulated LLM: returns a canned SQL string or raises a canned error."""

    def __init__(self, sql: str | None = None, error: Exception | None = None) -> None:
        self.sql = sql
        self.error = error
        self.questions: list[str] = []

    def generate_sql(self, question: str) -> str:
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        assert self.sql is not None
        return self.sql


class FakeQueryRunner:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[str] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.executed.append(sql)
        return self.rows


@pytest.fixture
def fake_llm() -> FakeSQLGenerator:
    return FakeSQLGenerator(sql="SELECT 1")


@pytest.fixture
def fake_db() -> FakeQueryRunner:
    return FakeQueryRunner()


@pytest.fixture
def client(fake_llm: FakeSQLGenerator, fake_db: FakeQueryRunner) -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    app.dependency_overrides[get_sql_generator] = lambda: fake_llm
    app.dependency_overrides[get_query_runner] = lambda: fake_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


__all__ = ["FakeQueryRunner", "FakeSQLGenerator", "LLMError", "todo"]
