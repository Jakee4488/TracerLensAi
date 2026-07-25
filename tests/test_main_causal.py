"""Proxy tests for the causal reasoning transport (marker + state_delta)."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import proxy.main as proxy_main


class DummyCredentials:
    token = "fake-token"

    def refresh(self, request):
        pass


class DummyStreamResponse:
    status_code = 200

    def __init__(self, body: bytes):
        self._body = body

    async def aread(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def install_dummy_engine(monkeypatch, stream_events, captured=None):
    monkeypatch.setenv("AGENT_ENGINE_ENDPOINT", "https://example.com/v1/reasoningEngines/123:query")
    monkeypatch.setattr("google.auth.default", lambda scopes: (DummyCredentials(), "fake-project"))
    body = ("\n".join(json.dumps(e) for e in stream_events)).encode("utf-8")

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, json=None, headers=None):
            if captured is not None:
                captured["payload"] = json
            return DummyStreamResponse(body)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)


# ── Activation marker ─────────────────────────────────────────────────────────

def test_marker_prepended_when_causal_enabled(client: TestClient, monkeypatch):
    captured = {}
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": "hi"}]}}], captured)
    response = client.post("/analyze-prompt", json={"prompt": "Why?", "causal_reasoning": True})
    assert response.status_code == 200
    message = captured["payload"]["input"]["message"]
    assert message.startswith(proxy_main.CAUSAL_MODE_MARKER)
    assert message.endswith("Why?")


def test_no_marker_when_causal_disabled(client: TestClient, monkeypatch):
    captured = {}
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": "hi"}]}}], captured)
    client.post("/analyze-prompt", json={"prompt": "Why?", "causal_reasoning": False})
    assert captured["payload"]["input"]["message"] == "Why?"


# ── state_delta transport ─────────────────────────────────────────────────────

def test_state_delta_populates_causal_fields(client: TestClient, monkeypatch):
    graph = {"nodes": [{"id": "a", "label": "A", "kind": "process", "status": "done"}],
             "edges": [], "critical_path": ["a"], "version": 1}
    estimand = {"treatment": "t", "outcome": "y", "identifiable": True,
                "estimand_type": "backdoor", "adjustment_set": ["z"]}
    effect = {"method": "backdoor.linear_regression", "point": 2.0}
    events = [
        {"content": {"parts": [{"text": '{"goal": "intermediate json"}'}]},
         "usage_metadata": {"total_token_count": 100}},
        {"actions": {"state_delta": {"causal_steps": ["[graph] decomposed"],
                                     "causal_graph": graph,
                                     "causal_estimand": estimand,
                                     "causal_effect": effect}}},
        {"content": {"parts": [{"text": "step output noise"}]},
         "usage_metadata": {"total_token_count": 50}},
        {"actions": {"state_delta": {"causal_final_answer": "The clean answer.",
                                     "causal_status": {"phase": "complete"},
                                     "other_key": "ignored"}},
         "usage_metadata": {"total_token_count": 25}},
    ]
    install_dummy_engine(monkeypatch, events)
    data = client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}).json()

    # Final answer preferred over concatenated intermediate text.
    assert data["response"] == "The clean answer."
    assert data["causal_reasoning_steps"] == ["[graph] decomposed"]
    assert data["causal_graph"] == graph
    assert data["causal_status"] == {"phase": "complete"}
    # DoWhy identification/effect ride the same transport.
    assert data["causal_estimand"] == estimand
    assert data["causal_effect"] == effect
    # Token counts are summed across the multi-agent turn.
    assert data["total_token_count"] == 175


def test_state_delta_camel_case_accepted(client: TestClient, monkeypatch):
    events = [{"actions": {"stateDelta": {"causal_final_answer": "camel"}}}]
    install_dummy_engine(monkeypatch, events)
    data = client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}).json()
    assert data["response"] == "camel"


def test_non_causal_response_unchanged(client: TestClient, monkeypatch):
    events = [
        {"content": {"parts": [{"text": "Hello! "}]}},
        {"content": {"parts": [{"text": "How can I help?"}]},
         "usage_metadata": {"total_token_count": 42}},
    ]
    install_dummy_engine(monkeypatch, events)
    data = client.post("/analyze-prompt", json={"prompt": "Q"}).json()
    assert data["response"] == "Hello! How can I help?"
    assert data["total_token_count"] == 42
    assert data["causal_reasoning_steps"] == []
    assert data["causal_graph"] is None


# ── Fenced-block fallback transport ───────────────────────────────────────────

def test_fenced_block_fallback(client: TestClient, monkeypatch):
    payload = {"steps": ["s1 ok"], "graph": {"nodes": [{"id": "a"}], "edges": []},
               "status": {"phase": "complete"},
               "estimand": {"treatment": "t", "outcome": "y", "identifiable": True},
               "effect": {"method": "backdoor.linear_regression", "point": 1.0},
               "final_answer": "Fallback answer."}
    text = f"intermediate noise\n```causal-json\n{json.dumps(payload)}\n```"
    events = [{"content": {"parts": [{"text": text}]}}]
    install_dummy_engine(monkeypatch, events)
    data = client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}).json()
    assert data["response"] == "Fallback answer."
    assert data["causal_reasoning_steps"] == ["s1 ok"]
    assert data["causal_graph"]["nodes"] == [{"id": "a"}]
    assert data["causal_estimand"]["treatment"] == "t"
    assert data["causal_effect"]["point"] == 1.0


def test_fallback_not_parsed_when_causal_disabled(client: TestClient, monkeypatch):
    text = '```causal-json\n{"steps": ["x"], "final_answer": "y"}\n```'
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": text}]}}])
    data = client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": False}).json()
    assert data["causal_reasoning_steps"] == []


def test_marker_stripped_from_response_text(client: TestClient, monkeypatch):
    text = f"echoing {proxy_main.CAUSAL_MODE_MARKER} back"
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": text}]}}])
    data = client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}).json()
    assert proxy_main.CAUSAL_MODE_MARKER not in data["response"]


# ── Mock path (no AGENT_ENGINE_ENDPOINT) ──────────────────────────────────────

def test_mock_path_returns_canned_graph(client: TestClient, monkeypatch):
    monkeypatch.delenv("AGENT_ENGINE_ENDPOINT", raising=False)
    data = client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}).json()
    assert len(data["causal_reasoning_steps"]) == 3
    assert {n["id"] for n in data["causal_graph"]["nodes"]} == {"inputs", "analysis", "outcome"}
    assert data["causal_status"] == {"phase": "complete"}
    # Canned identification/effect so the estimand card develops offline.
    assert data["causal_estimand"]["identifiable"] is True
    assert data["causal_estimand"]["adjustment_set"] == ["season", "income"]
    assert data["causal_effect"]["method"]
    assert len(data["causal_effect"]["refutations"]) == 2


def test_mock_path_no_graph_when_disabled(client: TestClient, monkeypatch):
    monkeypatch.delenv("AGENT_ENGINE_ENDPOINT", raising=False)
    data = client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": False}).json()
    assert data["causal_reasoning_steps"] == []
    assert data["causal_graph"] is None
    assert data["causal_estimand"] is None
    assert data["causal_effect"] is None


# ── Fallback extractor unit tests ─────────────────────────────────────────────

def test_extract_causal_fallback_roundtrip():
    payload, cleaned = proxy_main._extract_causal_fallback(
        'before ```causal-json\n{"steps": [1]}\n``` after')
    assert payload == {"steps": [1]}
    assert "causal-json" not in cleaned and "before" in cleaned and "after" in cleaned


def test_extract_causal_fallback_bad_json():
    payload, cleaned = proxy_main._extract_causal_fallback("```causal-json\n{broken: json,}\n```")
    assert payload is None
    assert "causal-json" not in cleaned


def test_extract_causal_fallback_no_block():
    payload, cleaned = proxy_main._extract_causal_fallback("plain text")
    assert payload is None and cleaned == "plain text"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
