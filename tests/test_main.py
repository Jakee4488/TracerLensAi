from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from src.main import app

client = TestClient(app)

def test_health_check():
    """Test that the health endpoint returns 200 and healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_analyze_prompt():
    """Test that /analyze-prompt returns an AnalysisReport."""
    payload = {"prompt": "Write a python script to calculate compound interest and refund my payment"}
    response = client.post("/analyze-prompt", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    
    assert "features" in data
    assert "inference" in data
    assert "token_prediction" in data
    assert "causal_analysis" in data
    assert "synthetic_trace" in data
    assert "observability_metrics" in data
    assert "optimized_prompt" in data
    
    # Check shape of token prediction
    prediction = data["token_prediction"]
    assert "prompt_tokens" in prediction
    assert "estimated_total_tokens" in prediction
