import pytest
from unittest.mock import AsyncMock
from src.ai_gateway.fallback_manager import FallbackManager
from src.ai_gateway.interface import AgentResponse

@pytest.mark.asyncio
async def test_fallback_success_on_first_try():
    mock_client = AsyncMock()
    # Mock the response so the first model succeeds
    mock_client.generate_response.return_value = AgentResponse(
        content="Success!",
        provider="vertex-ai",
        model_name="gemini-2.5-flash",
        error=None
    )
    
    manager = FallbackManager(mock_client)
    result = await manager.generate_with_fallback("test prompt", "system prompt")
    
    assert result.content == "Success!"
    assert result.model_name == "gemini-2.5-flash"
    # Ensure the client was only called once (it didn't need to fallback)
    assert mock_client.generate_response.call_count == 1

@pytest.mark.asyncio
async def test_fallback_uses_second_model():
    mock_client = AsyncMock()
    
    # First model fails, second model succeeds
    fail_response = AgentResponse(content="", provider="vertex-ai", model_name="gemini-2.5-flash", error="Rate limit")
    success_response = AgentResponse(content="Success on try 2!", provider="vertex-ai", model_name="gemini-2.5-pro", error=None)
    
    mock_client.generate_response.side_effect = [fail_response, success_response]
    
    manager = FallbackManager(mock_client)
    result = await manager.generate_with_fallback("test prompt", "system prompt")
    
    assert result.content == "Success on try 2!"
    assert result.model_name == "gemini-2.5-pro"
    # Ensure the client was called twice because the first one failed
    assert mock_client.generate_response.call_count == 2
