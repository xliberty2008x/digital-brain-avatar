from typing import Any

from pydantic import BaseModel, Field


class JournalAppendPlan(BaseModel):
    """The immutable core payload for the server-owned journal append."""

    content: str = Field(..., min_length=1)
    timestamp: str = Field(..., min_length=1)
    mood: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class PostAppendMutation(BaseModel):
    """An idempotent graph mutation that runs after the JournalEntry exists."""

    query: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class QueriesOutput(BaseModel):
    journal: JournalAppendPlan
    post_append_mutations: list[PostAppendMutation] = Field(default_factory=list)
