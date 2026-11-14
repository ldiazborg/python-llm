from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500, examples=["¿Cuánto vendimos en España en marzo?"])


class AskResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


class ErrorResponse(BaseModel):
    error: str = Field(description="Machine-readable code, e.g. llm_unavailable, invalid_query")
    detail: str = Field(description="Human-readable explanation")
    sql: str | None = Field(default=None, description="The rejected SQL, when there is one")
