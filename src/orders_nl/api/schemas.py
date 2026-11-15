"""Request/response models of the HTTP API (also drive validation and /docs)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500, examples=["¿Cuánto vendimos en España en marzo?"])
    natural_answer: bool = Field(
        default=True,
        description="Also return the result as a sentence (one extra LLM call). False = data only.",
    )


class AskResponse(BaseModel):
    question: str
    answer: str | None = Field(
        default=None,
        description="The result in natural language; null when natural_answer=false",
    )
    answer_source: Literal["llm", "template"] | None = Field(
        default=None,
        description="'llm': written by the model, every number checked against the data; "
        "'template': deterministic fallback text",
    )
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


class ErrorResponse(BaseModel):
    error: str = Field(description="Machine-readable code, e.g. llm_unavailable, invalid_query")
    detail: str = Field(description="Human-readable explanation")
    sql: str | None = Field(default=None, description="The rejected SQL, when there is one")
