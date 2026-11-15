"""Request/response models of the HTTP API (also drive validation and /docs).

Pydantic validates incoming JSON against these models before the endpoint
runs (a too-short question gets FastAPI's automatic 422), and FastAPI uses
them to generate the OpenAPI schema shown at /docs.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Body of POST /ask."""

    # 3..500 characters: rejects empty/accidental input and keeps prompts small.
    question: str = Field(min_length=3, max_length=500, examples=["¿Cuánto vendimos en España en marzo?"])
    natural_answer: bool = Field(
        default=True,
        description="Also return the result as a sentence (one extra LLM call). False = data only.",
    )


class AskResponse(BaseModel):
    """Successful answer to POST /ask: the sentence, the SQL that ran, and the data."""

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
    sql: str  # the validated SQL that was actually executed (LIMIT included)
    columns: list[str]  # column names, in SELECT order
    rows: list[dict[str, Any]]  # one {column: value} object per row
    row_count: int


class ErrorResponse(BaseModel):
    """Body of every error response (see api/errors.py for the codes)."""

    error: str = Field(description="Machine-readable code, e.g. llm_unavailable, invalid_query")
    detail: str = Field(description="Human-readable explanation")
    sql: str | None = Field(default=None, description="The rejected SQL, when there is one")
