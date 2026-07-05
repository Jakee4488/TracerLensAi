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
    """Test the /inquire-traced endpoint by mocking the backend agent orchestrator."""
    # Mock the AgentOrchestrator and its process_inquiry method
    mock_agent = AsyncMock()
    mock_agent.process_inquiry.return_value = {
        "response": "Hello from mock agent",
        "escalated": False,
        "metrics": {"latency_ms": 100, "prompt_tokens": 10, "completion_tokens": 10},
        "trace": [{"description": "Mocked Step", "latency_ms": 50, "tokens": 10, "step_type": "response", "token_source": "gateway", "token_fallback_used": False}]
    }
    mock_get_agent.return_value = mock_agent

    # Send a request to the /inquire-traced endpoint
    payload = {
        "user_id": "test-123",
        "prompt": "Hello!"
    }
    response = client.post("/inquire-traced", json=payload)
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["response"] == "Hello from mock agent"
    assert data["escalated"] is False
    assert len(data["trace"]) == 1
    assert data["trace"][0]["token_source"] == "gateway"
    
    # Ensure the orchestrator was called with the right parameters
    mock_agent.process_inquiry.assert_called_once_with(
        user_id="test-123",
        prompt="Hello!",
        session_history=[]
    )

@patch("src.main.evaluate_workflow")
def test_evaluate_trace(mock_evaluate):
    """Test the /evaluate-trace endpoint."""
    mock_evaluate.return_value = {
        "dashboard": {
            "step_efficiency_ratio": "1.0",
            "loop_rate": "0%",
            "token_velocity": "100 tokens/task",
            "latency_bottleneck": "None"
        },
        "root_cause_analysis": ["All good"],
        "rectified_prompt": "N/A"
    }

    payload = {
        "original_prompt": "Do X",
        "ideal_steps": 3,
        "trace": [{"description": "Mocked Step", "latency_ms": 50, "tokens": 10, "step_type": "response"}]
    }
    response = client.post("/evaluate-trace", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["dashboard"]["loop_rate"] == "0%"
