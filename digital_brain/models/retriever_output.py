from typing import Optional

from pydantic import BaseModel, Field


class DuplicateCandidate(BaseModel):
    """Read-only evidence that two entity mentions may refer to the same thing.

    Not a mutation command. Downstream systems must not auto-merge or delete
    based on this structure; it is proposal/input material only.
    """

    entity_a_id: Optional[str] = Field(
        default=None,
        description="ID of first entity if known in the graph; null if not yet created",
    )
    entity_a_name: str = Field(description="Name/surface form of first entity")
    entity_b_id: Optional[str] = Field(
        default=None,
        description="ID of second entity if known in the graph; null if not yet created",
    )
    entity_b_name: str = Field(description="Name/surface form of second entity")
    reason: str = Field(description="Why these may be the same entity")
    evidence: Optional[str] = Field(
        default=None,
        description="Optional supporting evidence (shared connections, weight, etc.)",
    )


class RetrieverOutput(BaseModel):
    """Structured output from context_retriever."""

    context_summary: str = Field(description="Summary of retrieved context")
    duplicate_candidates: list[DuplicateCandidate] = Field(
        default_factory=list,
        description="Report-only candidate pairs; never auto-applied mutations",
    )
