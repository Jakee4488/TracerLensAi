from fastapi.testclient import TestClient

def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_analyze_prompt_mock(client: TestClient):
    # This tests the mock behavior when AGENT_ENGINE_ENDPOINT is not set
    response = client.post("/analyze-prompt", json={
        "prompt": "Hello",
        "causal_reasoning": False,
        "web_search": False,
        "model_name": "gemini-2.5-flash",
        "chat_id": None
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "Agent Proxy configured." in data["response"]
