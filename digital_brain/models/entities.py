from pydantic import BaseModel, Field
from typing import List, Optional


class Entity(BaseModel):
    type: str = Field(..., description="Entity type (Person, Topic, Organization, Place, etc)")
    name: str = Field(..., description="Entity name")
    relation: Optional[str] = Field(None, description="Relation to user if applicable")


class ExtractedEvent(BaseModel):
    description: str = Field(..., description="Description of the event")
    type: str = Field(..., description="Type of event (conflict, achievement, life_event, business, etc)")
    timestamp: str = Field(..., description="Estimated timestamp (YYYY-MM-DD)")
    source_date: Optional[str] = Field(None, description="Original entry date if input contains multiple dated entries")
    is_clarified: bool = Field(..., description="True if time is clear, False if needs clarification")


class JournalEntryExtraction(BaseModel):
    """Represents extraction from a single dated journal entry."""
    entry_date: str = Field(..., description="Date of this specific journal entry (YYYY-MM-DD)")
    mood: str = Field(..., description="Emotional state for this entry")
    entities: List[Entity] = Field(default_factory=list, description="Entities mentioned in this entry")
    events: List[ExtractedEvent] = Field(default_factory=list, description="Events described in this entry")


class EntityOutput(BaseModel):
    """Output schema for entity extraction. Supports both single and batch inputs."""
    entries: List[JournalEntryExtraction] = Field(..., description="List of extracted journal entries")
    search_query: str = Field(..., description="Combined search query for vector search across all entries")
