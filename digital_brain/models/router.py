from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class RouterOutput(BaseModel):
    route: Literal["SKIP", "CLARIFY", "READ", "WRITE"] = Field(
        ..., 
        description="The selected processing route"
    )
    missing: Optional[List[str]] = Field(
        default=None,
        description="For CLARIFY: what information is missing (event, who)"
    )
