from pydantic import BaseModel
from typing import Optional

class AgentResponse(BaseModel):
    content: str
    provider: str
    model_name: str
    error: Optional[str] = None
