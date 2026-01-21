from pydantic import BaseModel, Field
from typing import Optional


class MergeCommand(BaseModel):
    """Command to merge duplicate entities."""
    keep_id: str = Field(description="ID of entity to keep (usually higher weight)")
    keep_name: str = Field(description="Name of entity to keep")
    remove_id: str = Field(description="ID of entity to remove (duplicate)")
    remove_name: str = Field(description="Name of entity to remove")
    reason: str = Field(description="Why these should be merged")


class RetrieverOutput(BaseModel):
    """Structured output from context_retriever."""
    context_summary: str = Field(description="Summary of retrieved context")
    merge_commands: list[MergeCommand] = Field(
        default_factory=list,
        description="List of merge commands for duplicate entities detected"
    )
