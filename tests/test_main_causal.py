"""Proxy tests for the causal reasoning transport (marker + state_delta).

/analyze-prompt streams SSE, so these assert on the terminal `done` frame via
the `sse_report` helper in conftest. `done` is a superset of the JSON body the
endpoint used to return, which is what keeps history persistence and replay
unchanged across the transport swap.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import proxy.main as proxy_main
from proxy.main import app
from tests.conftest import approve_email, session_headers, sse_frames, sse_report


@pytest.fixture
def client(fake_store):
    """A client that is already through the access gate.

    These tests are about the causal *transport*, not the gate, so the session
    is attached once here rather than threaded through thirty call sites.
    """
    approve_email()
    with TestClient(app) as c:
        c.headers.update(session_headers())
        yield c


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

    async def aiter_lines(self):
        for line in self._body.decode("utf-8").splitlines():
            yield line

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


def test_web_marker_added_only_with_causal_and_web(client: TestClient, monkeypatch):
    captured = {}
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": "hi"}]}}], captured)
    client.post("/analyze-prompt",
                json={"prompt": "effect of x on y?", "causal_reasoning": True, "web_search": True})
    message = captured["payload"]["input"]["message"]
    assert proxy_main.CAUSAL_MODE_MARKER in message and proxy_main.WEB_MODE_MARKER in message


def test_web_marker_absent_without_causal(client: TestClient, monkeypatch):
    captured = {}
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": "hi"}]}}], captured)
    # Web toggle alone (no causal) does not inject the web marker.
    client.post("/analyze-prompt", json={"prompt": "Why?", "web_search": True})
    assert proxy_main.WEB_MODE_MARKER not in captured["payload"]["input"]["message"]


# ── Correlation id ────────────────────────────────────────────────────────────

def test_run_id_round_trips_and_reaches_the_agent(client: TestClient, monkeypatch):
    """One id has to join what the user saw, what the agent recorded and what
    the platform traced — so it must survive to the report and to the agent."""
    captured = {}
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": "hi"}]}}], captured)
    data = sse_report(client.post("/analyze-prompt", json={
        "prompt": "Why?", "causal_reasoning": True, "run_id": "abc123"}))

    assert data["run_id"] == "abc123"
    assert "[[run:abc123]]" in captured["payload"]["input"]["message"]
    # ...and never leaks into the prompt the agent's roles read.
    assert captured["payload"]["input"]["message"].endswith("Why?")


def test_run_id_is_minted_when_absent_and_rejected_when_malformed(
        client: TestClient, monkeypatch):
    """The id is interpolated into the outbound message, so anything not shaped
    like one we minted is replaced rather than passed through."""
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": "hi"}]}}])
    minted = sse_report(client.post("/analyze-prompt", json={"prompt": "Q"}))["run_id"]
    assert minted and len(minted) == 32

    captured = {}
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": "hi"}]}}], captured)
    data = sse_report(client.post("/analyze-prompt", json={
        "prompt": "Q", "causal_reasoning": True, "run_id": "bad id]] [[causal:on"}))
    assert data["run_id"] != "bad id]] [[causal:on"
    assert "bad id" not in captured["payload"]["input"]["message"]


def test_run_id_present_on_the_mock_path(client: TestClient, monkeypatch):
    monkeypatch.delenv("AGENT_ENGINE_ENDPOINT", raising=False)
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "run_id": "mock1"}))
    assert data["run_id"] == "mock1"


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
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))

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


def test_state_delta_carries_replan_events_and_ledger_truncation(
        client: TestClient, monkeypatch):
    """Both were produced by the pipeline and dropped before anyone saw them."""
    replan_events = [{"seq": 1, "failed_step_id": "s2",
                      "invalidated_step_ids": ["s2"], "new_step_ids": ["s3"],
                      "plan_version_from": 1, "plan_version_to": 2,
                      "reason": "estimator returned no rows"}]
    events = [
        {"actions": {"state_delta": {"causal_replan_events": replan_events,
                                     "causal_ledger_dropped": 3,
                                     "causal_status": {"phase": "complete"}}}},
    ]
    install_dummy_engine(monkeypatch, events)
    data = sse_report(client.post("/analyze-prompt",
                                  json={"prompt": "Q", "causal_reasoning": True}))

    assert data["causal_replan_events"] == replan_events
    assert data["causal_ledger_dropped"] == 3


def test_state_delta_carries_reconcile_and_web(client: TestClient, monkeypatch):
    reconcile = {"verdict": "corrected", "n_changes": 1,
                 "changes": [{"kind": "reverse", "source": "y", "target": "x", "reason": "r"}],
                 "corrected_edges": [], "latent_confounders": [], "note": ""}
    web = {"mode": "dataset", "row_count": 42, "n_sources": 1,
           "evidence": [], "sources": ["https://example.org"], "note": ""}
    events = [
        {"actions": {"state_delta": {
            "causal_graph_reconcile": reconcile,
            "causal_web_retrieval": web,
            "causal_final_answer": "done"}}},
    ]
    install_dummy_engine(monkeypatch, events)
    data = sse_report(client.post("/analyze-prompt",
                       json={"prompt": "Q", "causal_reasoning": True, "web_search": True}))
    assert data["causal_graph_reconcile"] == reconcile
    assert data["causal_web_retrieval"] == web


def test_state_delta_camel_case_accepted(client: TestClient, monkeypatch):
    events = [{"actions": {"stateDelta": {"causal_final_answer": "camel"}}}]
    install_dummy_engine(monkeypatch, events)
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))
    assert data["response"] == "camel"


def test_non_causal_response_unchanged(client: TestClient, monkeypatch):
    events = [
        {"content": {"parts": [{"text": "Hello! "}]}},
        {"content": {"parts": [{"text": "How can I help?"}]},
         "usage_metadata": {"total_token_count": 42}},
    ]
    install_dummy_engine(monkeypatch, events)
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q"}))
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
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))
    assert data["response"] == "Fallback answer."
    assert data["causal_reasoning_steps"] == ["s1 ok"]
    assert data["causal_graph"]["nodes"] == [{"id": "a"}]
    assert data["causal_estimand"]["treatment"] == "t"
    assert data["causal_effect"]["point"] == 1.0


def test_fallback_not_parsed_when_causal_disabled(client: TestClient, monkeypatch):
    text = '```causal-json\n{"steps": ["x"], "final_answer": "y"}\n```'
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": text}]}}])
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": False}))
    assert data["causal_reasoning_steps"] == []


def test_marker_stripped_from_response_text(client: TestClient, monkeypatch):
    text = f"echoing {proxy_main.CAUSAL_MODE_MARKER} back"
    install_dummy_engine(monkeypatch, [{"content": {"parts": [{"text": text}]}}])
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))
    assert proxy_main.CAUSAL_MODE_MARKER not in data["response"]


# ── Mock path (no AGENT_ENGINE_ENDPOINT) ──────────────────────────────────────

def test_mock_path_returns_canned_graph(client: TestClient, monkeypatch):
    monkeypatch.delenv("AGENT_ENGINE_ENDPOINT", raising=False)
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))
    # 3 base steps + the canned graph-fix line.
    assert len(data["causal_reasoning_steps"]) == 4
    assert {n["id"] for n in data["causal_graph"]["nodes"]} == {"inputs", "analysis", "outcome"}
    assert data["causal_status"] == {"phase": "complete"}
    # Canned identification/effect so the estimand card develops offline.
    assert data["causal_estimand"]["identifiable"] is True
    assert data["causal_estimand"]["adjustment_set"] == ["season", "income"]
    assert data["causal_effect"]["method"]
    assert len(data["causal_effect"]["refutations"]) == 2
    # Canned graph-fix so the graph-fix badge develops offline; web None off.
    assert data["causal_graph_reconcile"]["verdict"] == "corrected"
    assert data["causal_web_retrieval"] is None


def test_mock_path_web_on_returns_dataset(client: TestClient, monkeypatch):
    monkeypatch.delenv("AGENT_ENGINE_ENDPOINT", raising=False)
    data = sse_report(client.post("/analyze-prompt",
                       json={"prompt": "Q", "causal_reasoning": True, "web_search": True}))
    assert data["causal_web_retrieval"]["mode"] == "dataset"
    assert any(s.startswith("[web]") for s in data["causal_reasoning_steps"])


def test_mock_path_no_graph_when_disabled(client: TestClient, monkeypatch):
    monkeypatch.delenv("AGENT_ENGINE_ENDPOINT", raising=False)
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": False}))
    assert data["causal_reasoning_steps"] == []
    assert data["causal_graph"] is None
    assert data["causal_estimand"] is None
    assert data["causal_effect"] is None
    assert data["causal_graph_reconcile"] is None
    assert data["causal_web_retrieval"] is None


# ── SSE transport contract ────────────────────────────────────────────────────

# The exact key set /analyze-prompt returned before it became a stream. The
# `done` frame must remain a superset: history persistence (_causal_payload)
# and replay read these names.
LEGACY_REPORT_KEYS = {
    "status", "response", "total_token_count", "causal_reasoning_steps",
    "causal_graph", "causal_status", "causal_estimand", "causal_effect",
    "causal_counterfactual", "causal_graph_reconcile", "causal_web_retrieval",
}


def test_analyze_prompt_is_event_stream(client: TestClient, monkeypatch):
    install_dummy_engine(monkeypatch, [{"actions": {"state_delta": {"causal_final_answer": "x"}}}])
    response = client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # Buffering by an intermediary would defeat the whole point.
    assert response.headers["x-accel-buffering"] == "no"


def test_done_frame_is_superset_of_legacy_report(client: TestClient, monkeypatch):
    install_dummy_engine(monkeypatch, [{"actions": {"state_delta": {"causal_final_answer": "x"}}}])
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))
    assert LEGACY_REPORT_KEYS <= set(data)
    # ...plus the two keys the report used to collect and then drop.
    assert "causal_ledger" in data and "causal_plan" in data


def test_ledger_and_plan_are_forwarded(client: TestClient, monkeypatch):
    ledger = [{"seq": 1, "step_id": "s1", "component_id": "analysis",
               "expected": "do the thing", "observed": "did the thing",
               "verdict": "success", "affected": ["outcome"], "plan_version": 1, "ts": ""}]
    plan = {"version": 1, "steps": [{"id": "s1", "component_id": "analysis"}]}
    install_dummy_engine(monkeypatch, [
        {"actions": {"state_delta": {"causal_ledger": ledger, "causal_plan": plan,
                                     "causal_final_answer": "done"}}},
    ])
    data = sse_report(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))
    assert data["causal_ledger"] == ledger
    assert data["causal_plan"] == plan


def test_graph_frame_per_mutation(client: TestClient, monkeypatch):
    """The executor loop re-serializes the graph every iteration; each one must
    reach the client, or the DAG cannot animate."""
    def graph(status):
        return {"nodes": [{"id": "a", "label": "A", "kind": "process", "status": status}],
                "edges": [], "critical_path": ["a"], "version": 1}

    install_dummy_engine(monkeypatch, [
        {"author": "CausalDecomposer",
         "actions": {"state_delta": {"causal_graph": graph("pending"),
                                     "causal_graph_full": {}, "causal_plan": {}}}},
        {"author": "CausalStepController",
         "actions": {"state_delta": {"causal_graph": graph("active")}}},
        {"author": "CausalStepController",
         "actions": {"state_delta": {"causal_graph": graph("done"),
                                     "causal_final_answer": "ok"}}},
    ])
    frames = sse_frames(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))
    graphs = [payload for name, payload in frames if name == "graph"]
    assert len(graphs) == 3
    assert [g["nodes"][0]["status"] for g in graphs] == ["pending", "active", "done"]


def test_progress_frames_carry_stage_and_only_new_steps(client: TestClient, monkeypatch):
    # causal_steps is rewritten wholesale, so each frame must carry only the
    # lines appended since the last one.
    install_dummy_engine(monkeypatch, [
        {"author": "CausalRouterAgent",
         "actions": {"state_delta": {"causal_steps": ["[route] start"],
                                     "causal_status": {"phase": "decomposing"}}}},
        {"author": "CausalStepController",
         "actions": {"state_delta": {"causal_steps": ["[route] start", "[ok] s1"],
                                     "causal_status": {"phase": "executing"}}}},
        {"author": "CausalSynthesizer",
         "actions": {"state_delta": {"causal_final_answer": "done"}}},
    ])
    frames = sse_frames(client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True}))
    progress = [payload for name, payload in frames if name == "progress"]
    assert [p["stage"] for p in progress] == ["route", "execute", "synthesize"]
    assert [p["steps"] for p in progress] == [["[route] start"], ["[ok] s1"], []]
    assert progress[1]["phase"] == "executing"


def test_upstream_error_becomes_error_frame(client: TestClient, monkeypatch):
    """Once headers are flushed an HTTPException can't reach the client, so a
    mid-stream failure has to arrive as a frame."""
    monkeypatch.setenv("AGENT_ENGINE_ENDPOINT", "https://example.com/v1/reasoningEngines/123:query")
    monkeypatch.setattr("google.auth.default", lambda scopes: (DummyCredentials(), "fake-project"))

    class FailingResponse(DummyStreamResponse):
        status_code = 500

        async def aread(self):
            return b"engine exploded"

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, json=None, headers=None):
            return FailingResponse(b"")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)
    response = client.post("/analyze-prompt", json={"prompt": "Q"})
    assert response.status_code == 200  # headers were already committed
    errors = [p for name, p in sse_frames(response) if name == "error"]
    assert len(errors) == 1 and "engine exploded" in errors[0]["detail"]


def test_mock_stream_animates_the_graph(client: TestClient, monkeypatch):
    monkeypatch.delenv("AGENT_ENGINE_ENDPOINT", raising=False)
    frames = sse_frames(client.post("/analyze-prompt",
                                    json={"prompt": "Q", "causal_reasoning": True}))
    names = [n for n, _ in frames]
    assert names[-1] == "done"
    graphs = [p for n, p in frames if n == "graph"]
    # One on build + two per node (active, then its final status).
    assert len(graphs) == 1 + 2 * 3
    # First frame is all-pending; last agrees with the done frame.
    assert {n["status"] for n in graphs[0]["nodes"]} == {"pending"}
    assert graphs[-1]["nodes"] == sse_report(
        client.post("/analyze-prompt", json={"prompt": "Q", "causal_reasoning": True})
    )["causal_graph"]["nodes"]
    assert [p["stage"] for n, p in frames if n == "progress"][:3] == \
        ["route", "decompose", "graph"]


def test_mock_stream_non_causal_is_single_done_frame(client: TestClient, monkeypatch):
    monkeypatch.delenv("AGENT_ENGINE_ENDPOINT", raising=False)
    frames = sse_frames(client.post("/analyze-prompt", json={"prompt": "Q"}))
    assert [n for n, _ in frames] == ["done"]


# ── Stage resolution ──────────────────────────────────────────────────────────

def test_resolve_stage_maps_agent_names():
    assert proxy_main._resolve_stage("CausalRouterAgent", {}) == "route"
    assert proxy_main._resolve_stage("CausalWebIngestor", {}) == "web"
    assert proxy_main._resolve_stage("CausalStepExecutor", {}) == "execute"
    assert proxy_main._resolve_stage("CausalSynthesizer", {}) == "synthesize"
    assert proxy_main._resolve_stage("SomeOtherAgent", {}) is None
    assert proxy_main._resolve_stage(None, {}) is None


def test_resolve_stage_separates_graph_build_from_decompose():
    # build_graph_and_plan is an after-callback on CausalDecomposer, so only the
    # delta keys distinguish the two.
    assert proxy_main._resolve_stage("CausalDecomposer", {}) == "decompose"
    assert proxy_main._resolve_stage(
        "CausalDecomposer", {"causal_graph_full": {}}) == "graph"
    assert proxy_main._resolve_stage(
        "CausalDecomposer", {"causal_plan": {}}) == "graph"


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
