from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from src.main import app

client = TestClient(app)

def test_health_check():
    """Test that the health endpoint returns 200 and healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

@patch("src.main.get_agent")
def test_process_inquiry(mock_get_agent):
    """Test the /inquire endpoint by mocking the backend agent orchestrator."""
    # Mock the AgentOrchestrator and its process_inquiry method
    mock_agent = AsyncMock()
    mock_agent.process_inquiry.return_value = {
        "response": "Hello from mock agent",
        "escalated": False,
        "metrics": {"latency_ms": 100}
    }
    mock_get_agent.return_value = mock_agent

    # Send a request to the /inquire endpoint
    payload = {
        "user_id": "test-123",
        "prompt": "Hello!"
    }
    response = client.post("/inquire", json=payload)
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["response"] == "Hello from mock agent"
    assert data["escalated"] is False
    
    # Ensure the orchestrator was called with the right parameters
    mock_agent.process_inquiry.assert_called_once_with(
        user_id="test-123",
        prompt="Hello!",
        session_history=[]
    )
