import json

import httpx
import pytest
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

def test_analyze_prompt_reports_real_token_count(client: TestClient, monkeypatch):
    # Regression test: the proxy used to hardcode total_token_count to 0 for
    # real Agent Engine responses, so the UI's token badge never updated.
    monkeypatch.setenv("AGENT_ENGINE_ENDPOINT", "https://example.com/v1/reasoningEngines/123:query")

    class DummyCredentials:
        token = "fake-token"

        def refresh(self, request):
            pass

    monkeypatch.setattr(
        "google.auth.default",
        lambda scopes: (DummyCredentials(), "fake-project"),
    )

    stream_events = [
        json.dumps({"content": {"parts": [{"text": "Hello! "}]}}),
        json.dumps({
            "content": {"parts": [{"text": "How can I help?"}]},
            "usage_metadata": {"total_token_count": 42},
        }),
    ]
    response_body = ("\n".join(stream_events)).encode("utf-8")

    class DummyStreamResponse:
        status_code = 200

        async def aread(self):
            return response_body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, json=None, headers=None):
            return DummyStreamResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

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
    assert data["response"] == "Hello! How can I help?"
    assert data["total_token_count"] == 42
