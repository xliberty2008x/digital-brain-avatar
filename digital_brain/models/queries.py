from pydantic import BaseModel, Field
from typing import List, Optional


class QueriesOutput(BaseModel):
    queries: List[str] = Field(..., description="List of Cypher queries")
