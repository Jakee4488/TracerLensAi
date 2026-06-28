from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any


class AgentResponse(BaseModel):
    """
    Unified response object from any LLM provider.
    """
    model_config = ConfigDict(protected_namespaces=())
    content: str = Field(...,
                         description="The textual response from the model.")
    provider: str = Field(
        ..., description="The provider used (e.g., 'vertex-gemini', 'vertex-claude').")
    model_name: str = Field(..., description="The exact model string used.")
    prompt_tokens: int = Field(0, description="Tokens in the input prompt.")
    completion_tokens: int = Field(
        0, description="Tokens in the output response.")
    latency_ms: float = Field(
        0.0, description="Time taken for the API call in milliseconds.")
    error: Optional[str] = Field(
        None, description="Error message if the call failed.")
    tool_calls: Optional[list[Dict[str, Any]]] = Field(
        None, description="Requested tool invocations.")
