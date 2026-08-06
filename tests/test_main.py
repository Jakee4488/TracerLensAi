import json
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

import proxy.access as proxy_access
import proxy.main as proxy_main
from tests.conftest import (
    TEST_EMAIL, approve_email, session_headers, sse_report,
)

# Every gated endpoint keys on the email hash, so the Firestore paths tests
# assert on are derived rather than hardcoded.
USER_KEY = proxy_access.email_key(TEST_EMAIL)

OTHER_EMAIL = "someone.else@example.com"
OTHER_KEY = proxy_access.email_key(OTHER_EMAIL)

# ── Proxy dependency drift guard ─────────────────────────────────────────────

def test_proxy_imports_are_covered_by_requirements():
    """Every third-party module proxy/ imports must be in requirements-proxy.txt.

    The Cloud Run image installs that slim file, not requirements.txt, so a new
    proxy import missing from it fails at *container start* rather than in CI.
    This catches the drift here instead.
    """
    import ast
    import pathlib
    import re
    import sys

    root = pathlib.Path(__file__).resolve().parents[1]

    # Distribution name -> the top-level module(s) it provides. Only entries
    # whose import name differs from the requirement name need listing.
    provides = {
        "firebase-admin": {"firebase_admin", "google"},   # also pulls google-cloud-firestore
        "google-auth": {"google"},
        "python-multipart": {"multipart"},                # imported by fastapi, not by us
    }
    stdlib = set(sys.stdlib_module_names)

    allowed = set()
    for line in (root / "requirements-proxy.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~\[]", line)[0].strip()
        allowed |= provides.get(name, {name.replace("-", "_")})

    imported = set()
    for path in (root / "proxy").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])

    missing = sorted(imported - stdlib - allowed - {"proxy"})
    assert not missing, (
        f"proxy/ imports {missing} which are absent from requirements-proxy.txt — "
        "the Cloud Run container would fail at startup. Add them there."
    )


def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_analyze_prompt_mock(client: TestClient, approved):
    # This tests the mock behavior when AGENT_ENGINE_ENDPOINT is not set
    response = client.post("/analyze-prompt", json={
        "prompt": "Hello",
        "causal_reasoning": False,
        "web_search": False,
        "model_name": "gemini-2.5-flash",
        "chat_id": None
    }, headers=approved)
    assert response.status_code == 200
    data = sse_report(response)
    assert data["status"] == "success"
    assert "Agent Proxy configured." in data["response"]

def test_analyze_prompt_reports_real_token_count(client: TestClient, approved, monkeypatch):
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
            "usage_metadata": {"total_token_count": 42,
                               "prompt_token_count": 30,
                               "candidates_token_count": 12},
        }),
    ]
    response_body = ("\n".join(stream_events)).encode("utf-8")

    class DummyStreamResponse:
        status_code = 200

        async def aread(self):
            return response_body

        async def aiter_lines(self):
            for line in response_body.decode("utf-8").splitlines():
                yield line

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
    }, headers=approved)
    assert response.status_code == 200
    data = sse_report(response)
    assert data["status"] == "success"
    assert data["response"] == "Hello! How can I help?"
    assert data["total_token_count"] == 42
    # The input/output split rides alongside the total for the dashboard.
    assert data["input_token_count"] == 30
    assert data["output_token_count"] == 12

# ── Auth & history tests ─────────────────────────────────────────────────────

def test_history_requires_auth(client: TestClient):
    assert client.get("/history").status_code == 401
    assert client.get("/history/some-chat").status_code == 401

def test_history_rejects_invalid_token(client: TestClient, fake_store):
    response = client.get("/history", headers={"Authorization": "Bearer forged.signature"})
    assert response.status_code == 401

def test_analyze_prompt_authenticated_persists_history(client: TestClient, fake_store, approved):
    # Mock engine path (no AGENT_ENGINE_ENDPOINT) with a signed-in user
    response = client.post("/analyze-prompt", json={
        "prompt": "What causes rain?",
        "chat_id": "chat-abc",
    }, headers=approved)
    assert response.status_code == 200

    # User profile upserted
    assert fake_store.docs[("users", USER_KEY)]["email"] == TEST_EMAIL
    # Conversation created with title from the prompt
    conv = fake_store.docs[("users", USER_KEY, "conversations", "chat-abc")]
    assert conv["title"] == "What causes rain?"
    assert conv["total_tokens"] == 10
    # Both messages stored
    msg_paths = [p for p in fake_store.docs
                 if len(p) == 6 and p[:5] == ("users", USER_KEY, "conversations", "chat-abc", "messages")]
    roles = sorted(fake_store.docs[p]["role"] for p in msg_paths)
    assert roles == ["ai", "user"]

def test_get_history_lists_conversations(client: TestClient, fake_store, approved):
    client.post("/analyze-prompt", json={"prompt": "First chat", "chat_id": "chat-1"}, headers=approved)
    client.post("/analyze-prompt", json={"prompt": "Second chat", "chat_id": "chat-2"}, headers=approved)

    response = client.get("/history", headers=approved)
    assert response.status_code == 200
    conversations = response.json()["conversations"]
    assert len(conversations) == 2
    assert {c["chat_id"] for c in conversations} == {"chat-1", "chat-2"}
    assert conversations[0]["title"] in ("First chat", "Second chat")

def test_get_history_messages_and_404(client: TestClient, fake_store, approved):
    client.post("/analyze-prompt", json={"prompt": "Hello there", "chat_id": "chat-1"}, headers=approved)

    response = client.get("/history/chat-1", headers=approved)
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Hello there"
    assert [m["role"] for m in data["messages"]] == ["user", "ai"]
    assert data["messages"][0]["content"] == "Hello there"

    assert client.get("/history/nope", headers=approved).status_code == 404

def test_history_is_not_shared_between_users(client: TestClient, fake_store, approved):
    """One visitor's conversations must never surface for another."""
    client.post("/analyze-prompt", json={"prompt": "Mine", "chat_id": "chat-mine"}, headers=approved)

    approve_email(OTHER_EMAIL)
    other = session_headers(OTHER_EMAIL)
    assert client.get("/history", headers=other).json()["conversations"] == []
    assert client.get("/history/chat-mine", headers=other).status_code == 404

@pytest.fixture
def fake_engine(monkeypatch):
    """Stub the Agent Engine transport; returns the dict of captured payloads."""
    monkeypatch.setenv("AGENT_ENGINE_ENDPOINT", "https://example.com/v1/reasoningEngines/123:query")
    captured = {}

    class DummyCredentials:
        token = "fake-token"

        def refresh(self, request):
            pass

    monkeypatch.setattr("google.auth.default", lambda scopes: (DummyCredentials(), "fake-project"))

    stream_body = json.dumps({
        "content": {"parts": [{"text": "It rains."}]},
        "usage_metadata": {"total_token_count": 7},
    }).encode("utf-8")

    class DummyStreamResponse:
        status_code = 200

        async def aread(self):
            return stream_body

        async def aiter_lines(self):
            for line in stream_body.decode("utf-8").splitlines():
                yield line

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, json=None, headers=None):
            captured["payload"] = json
            return DummyStreamResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)
    return captured

def test_analyze_prompt_authenticated_passes_user_key_to_engine(
        client: TestClient, fake_store, approved, fake_engine):
    response = client.post("/analyze-prompt", json={"prompt": "Why rain?", "chat_id": "chat-9"},
                           headers=approved)
    assert response.status_code == 200
    assert fake_engine["payload"]["input"]["user_id"] == USER_KEY
    # Real engine path also persists with the real token count
    conv = fake_store.docs[("users", USER_KEY, "conversations", "chat-9")]
    assert conv["total_tokens"] == 7

# ── Agent session isolation ──────────────────────────────────────────────────

ANON_A = "11111111-2222-4333-8444-555555555555"

def test_distinct_users_get_distinct_agent_sessions(client: TestClient, fake_store,
                                                    approved, fake_engine):
    """Agent session state persists upstream, so ids must not collide."""
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"}, headers=approved)
    first = fake_engine["payload"]["input"]["user_id"]

    approve_email(OTHER_EMAIL)
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers=session_headers(OTHER_EMAIL))
    second = fake_engine["payload"]["input"]["user_id"]

    assert first == USER_KEY
    assert second == OTHER_KEY
    assert first != second

def test_same_user_shares_one_agent_session(client: TestClient, fake_store, approved, fake_engine):
    client.post("/analyze-prompt", json={"prompt": "One", "chat_id": "c1"}, headers=approved)
    first = fake_engine["payload"]["input"]["user_id"]
    client.post("/analyze-prompt", json={"prompt": "Two", "chat_id": "c1"}, headers=approved)
    assert fake_engine["payload"]["input"]["user_id"] == first

def test_session_wins_over_anon_id_header(client: TestClient, fake_store, approved, fake_engine):
    headers = dict(approved)
    headers["X-Anon-Id"] = ANON_A
    client.post("/analyze-prompt", json={"prompt": "Why rain?", "chat_id": "c1"}, headers=headers)
    assert fake_engine["payload"]["input"]["user_id"] == USER_KEY

@pytest.mark.parametrize("bad", ["", "../../etc/passwd", "not-a-uuid", "x" * 500])
def test_malformed_anon_id_falls_back_to_unknown(bad):
    """The sessionless fallback is unreachable via the gate, but must stay safe.

    A spoofable header must never become an arbitrary agent session key, so
    anything unrecognized collapses into one bucket rather than passing through.
    """
    assert proxy_main._agent_user_id(None, bad) == "anon:unknown"

def test_anon_id_fallback_keeps_valid_uuids_distinct():
    assert proxy_main._agent_user_id(None, ANON_A) == f"anon:{ANON_A}"

def test_missing_chat_id_gets_throwaway_session(client: TestClient, fake_store,
                                                approved, fake_engine):
    client.post("/analyze-prompt", json={"prompt": "Hi"}, headers=approved)
    first = fake_engine["payload"]["input"]["session_id"]
    client.post("/analyze-prompt", json={"prompt": "Hi"}, headers=approved)
    second = fake_engine["payload"]["input"]["session_id"]
    assert first != second
    assert "default-session" not in (first, second)

# ── Causal payload persistence ───────────────────────────────────────────────

def _ai_message(fake_store, chat_id):
    prefix = ("users", USER_KEY, "conversations", chat_id, "messages")
    docs = [d for p, d in fake_store.docs.items() if len(p) == 6 and p[:5] == prefix]
    return next(d for d in docs if d["role"] == "ai")

def test_causal_turn_persists_and_replays_payload(client: TestClient, fake_store, approved):
    client.post("/analyze-prompt", json={
        "prompt": "Does price affect demand?",
        "chat_id": "chat-c",
        "causal_reasoning": True,
    }, headers=approved)

    stored = _ai_message(fake_store, "chat-c")["causal"]
    assert stored["causal_graph"]["nodes"]
    assert stored["causal_estimand"]["treatment"] == "price"

    # Round-trips through the API using the key names addAiMessage() reads.
    data = client.get("/history/chat-c", headers=approved).json()
    causal = data["messages"][1]["causal"]
    assert causal["causal_graph"] == stored["causal_graph"]
    assert causal["causal_effect"]["point"] == -1.42
    assert causal["causal_reasoning_steps"]

def test_non_causal_turn_omits_causal_key(client: TestClient, fake_store, approved):
    client.post("/analyze-prompt", json={"prompt": "Hello", "chat_id": "chat-p"}, headers=approved)
    assert "causal" not in _ai_message(fake_store, "chat-p")
    data = client.get("/history/chat-p", headers=approved).json()
    assert "causal" not in data["messages"][1]

def test_history_write_failure_still_returns_200(client: TestClient, fake_store, approved,
                                                 monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(proxy_main, "_save_exchange", boom)
    response = client.post("/analyze-prompt", json={
        "prompt": "Does price affect demand?",
        "chat_id": "chat-x",
        "causal_reasoning": True,
    }, headers=approved)
    # Persistence is best-effort: the turn still succeeds with its causal data.
    assert response.status_code == 200
    assert sse_report(response)["causal_graph"]["nodes"]

# ── Chat retention ───────────────────────────────────────────────────────────

def test_conversations_carry_an_expiry(client: TestClient, fake_store, approved):
    """The 24h deletion promised in the access modal has to be written down."""
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "chat-ttl"}, headers=approved)

    conv = fake_store.docs[("users", USER_KEY, "conversations", "chat-ttl")]
    lifetime = (conv["expires_at"] - conv["created_at"]).total_seconds()
    assert 23.9 * 3600 < lifetime <= 24 * 3600

    messages = [d for p, d in fake_store.docs.items()
                if len(p) == 6 and p[3] == "chat-ttl" and p[4] == "messages"]
    assert messages and all(m.get("expires_at") for m in messages)

# ── History pagination ───────────────────────────────────────────────────────

def test_history_paginates_with_cursor(client: TestClient, fake_store, approved):
    for i in range(5):
        client.post("/analyze-prompt", json={"prompt": f"Chat {i}", "chat_id": f"c{i}"},
                    headers=approved)

    first = client.get("/history?limit=2", headers=approved).json()
    assert len(first["conversations"]) == 2
    assert first["next_cursor"]

    second = client.get(f"/history?limit=2&cursor={first['next_cursor']}", headers=approved).json()
    assert len(second["conversations"]) == 2
    # No overlap between pages.
    ids = {c["chat_id"] for c in first["conversations"]}
    assert ids.isdisjoint({c["chat_id"] for c in second["conversations"]})

    last = client.get(f"/history?limit=2&cursor={second['next_cursor']}", headers=approved).json()
    assert len(last["conversations"]) == 1
    assert last["next_cursor"] is None

    seen = ids | {c["chat_id"] for c in second["conversations"]} | {
        c["chat_id"] for c in last["conversations"]}
    assert seen == {f"c{i}" for i in range(5)}

def test_history_cursor_accepts_percent_encoded_offset(client: TestClient, fake_store, approved):
    """The '+' in a UTC offset survives either encoding on the way back in."""
    for i in range(3):
        client.post("/analyze-prompt", json={"prompt": f"Chat {i}", "chat_id": f"c{i}"},
                    headers=approved)

    cursor = client.get("/history?limit=2", headers=approved).json()["next_cursor"]
    encoded = quote(cursor, safe="")
    assert client.get(f"/history?limit=2&cursor={encoded}", headers=approved).status_code == 200
    assert client.get(f"/history?limit=2&cursor={cursor}", headers=approved).status_code == 200

def test_history_malformed_cursor_400s(client: TestClient, fake_store, approved):
    client.post("/analyze-prompt", json={"prompt": "Chat", "chat_id": "c0"}, headers=approved)
    assert client.get("/history?cursor=not-a-date", headers=approved).status_code == 400

# ── Upload & attachment tests ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_uploads():
    proxy_main._uploads.clear()
    yield
    proxy_main._uploads.clear()

def test_upload_txt_success(client: TestClient, approved):
    response = client.post("/upload", files={"file": ("notes.txt", b"hello world", "text/plain")},
                           headers=approved)
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "notes.txt"
    assert data["size"] == 11
    assert data["text_chars"] == 11
    assert data["file_id"] in proxy_main._uploads

def test_upload_rejects_oversize(client: TestClient, approved):
    blob = b"x" * (proxy_main.MAX_UPLOAD_BYTES + 1)
    response = client.post("/upload", files={"file": ("big.txt", blob, "text/plain")},
                           headers=approved)
    assert response.status_code == 413
    assert "limit" in response.json()["detail"]

def test_upload_rejects_bad_type(client: TestClient, approved):
    response = client.post("/upload",
                           files={"file": ("virus.exe", b"MZ\x90\x00", "application/octet-stream")},
                           headers=approved)
    assert response.status_code == 415
    assert ".exe" in response.json()["detail"]

def test_upload_sanitizes_path_traversal_filename(client: TestClient, approved):
    response = client.post("/upload", files={"file": ("../../evil.md", b"# hi", "text/markdown")},
                           headers=approved)
    assert response.status_code == 200
    assert response.json()["filename"] == "evil.md"

def test_analyze_prompt_with_attachment_mock_ack(client: TestClient, approved):
    upload = client.post("/upload", files={"file": ("notes.txt", b"hello world", "text/plain")},
                         headers=approved)
    file_id = upload.json()["file_id"]

    response = client.post("/analyze-prompt", json={"prompt": "Summarise", "attachments": [file_id]},
                           headers=approved)
    assert response.status_code == 200
    assert "Attached files (1): notes.txt" in sse_report(response)["response"]

def test_analyze_prompt_unknown_attachment_404(client: TestClient, approved):
    response = client.post("/analyze-prompt", json={"prompt": "Hi", "attachments": ["nope"]},
                           headers=approved)
    assert response.status_code == 404

def test_attachment_ownership_enforced(client: TestClient, fake_store, approved):
    # Uploaded by one visitor → not referencable by another (404, not 403,
    # so ids aren't probeable).
    upload = client.post("/upload", files={"file": ("secret.txt", b"top secret", "text/plain")},
                         headers=approved)
    file_id = upload.json()["file_id"]

    approve_email(OTHER_EMAIL)
    stranger = client.post("/analyze-prompt", json={"prompt": "Hi", "attachments": [file_id]},
                           headers=session_headers(OTHER_EMAIL))
    assert stranger.status_code == 404

    owner = client.post("/analyze-prompt", json={"prompt": "Hi", "attachments": [file_id]},
                        headers=approved)
    assert owner.status_code == 200

def test_expired_upload_is_unreachable(client: TestClient, fake_store, approved, monkeypatch):
    """Attachments ride the same 24h clock as the chats they belong to."""
    monkeypatch.setenv("CHAT_RETENTION_HOURS", "0")
    upload = client.post("/upload", files={"file": ("notes.txt", b"hello", "text/plain")},
                         headers=approved)
    file_id = upload.json()["file_id"]

    response = client.post("/analyze-prompt", json={"prompt": "Hi", "attachments": [file_id]},
                           headers=approved)
    assert response.status_code == 404

def test_analyze_prompt_persists_attachment_names(client: TestClient, fake_store, approved):
    upload = client.post("/upload", files={"file": ("data.csv", b"a,b\n1,2", "text/csv")},
                         headers=approved)
    file_id = upload.json()["file_id"]

    response = client.post("/analyze-prompt", json={
        "prompt": "Analyse this", "chat_id": "chat-att", "attachments": [file_id],
    }, headers=approved)
    assert response.status_code == 200

    msg_paths = [p for p in fake_store.docs
                 if len(p) == 6 and p[:5] == ("users", USER_KEY, "conversations", "chat-att", "messages")]
    user_msgs = [fake_store.docs[p] for p in msg_paths if fake_store.docs[p]["role"] == "user"]
    assert user_msgs[0]["attachments"] == ["data.csv"]

def test_analyze_prompt_real_engine_includes_file_context(client: TestClient, fake_store,
                                                          approved, monkeypatch):
    monkeypatch.setenv("AGENT_ENGINE_ENDPOINT", "https://example.com/v1/reasoningEngines/123:query")
    captured = {}

    class DummyCredentials:
        token = "fake-token"

        def refresh(self, request):
            pass

    monkeypatch.setattr("google.auth.default", lambda scopes: (DummyCredentials(), "fake-project"))

    stream_body = json.dumps({
        "content": {"parts": [{"text": "Summarised."}]},
        "usage_metadata": {"total_token_count": 5},
    }).encode("utf-8")

    class DummyStreamResponse:
        status_code = 200

        async def aread(self):
            return stream_body

        async def aiter_lines(self):
            for line in stream_body.decode("utf-8").splitlines():
                yield line

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, json=None, headers=None):
            captured["payload"] = json
            return DummyStreamResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    upload = client.post("/upload", files={"file": ("notes.txt", b"the sky is blue", "text/plain")},
                         headers=approved)
    file_id = upload.json()["file_id"]

    response = client.post("/analyze-prompt", json={
        "prompt": "Summarise the file",
        "causal_reasoning": True,
        "attachments": [file_id],
    }, headers=approved)
    assert response.status_code == 200
    message = captured["payload"]["input"]["message"]
    # Causal marker must stay first for the agent's router, context before prompt.
    assert message.startswith(proxy_main.CAUSAL_MODE_MARKER)
    assert "--- Attached file: notes.txt ---" in message
    assert "the sky is blue" in message
    assert message.index("Attached file") < message.index("Summarise the file")


# ── Abort accounting ─────────────────────────────────────────────────────────

def test_aborting_a_stream_still_charges_the_tokens_already_burned(
        monkeypatch, fake_store):
    """Hitting Stop must not be a free ride.

    A client disconnect closes the SSE generator, raising GeneratorExit at the
    suspended yield. GeneratorExit derives from BaseException, so the streaming
    loop's `except Exception` never saw it and every statement after the loop —
    including the usage write — was skipped. That made the UI's Stop button an
    unlimited-quota bypass: abort at 99%, get billed zero, repeat.
    """
    import asyncio
    import time as _time

    approve_email()

    events = [json.dumps({
        "author": "CausalDecomposer",
        "usage_metadata": {"total_token_count": 42,
                           "prompt_token_count": 30,
                           "candidates_token_count": 12},
    })]

    class DummyStreamResponse:
        status_code = 200

        async def aread(self):
            return b""

        async def aiter_lines(self):
            for line in events:
                yield line
            # Upstream stays open; the client is the one that gives up.
            await asyncio.sleep(3600)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, *args, **kwargs):
            return DummyStreamResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    async def drive():
        stream = proxy_main._agent_stream(
            proxy_main.PromptRequest(prompt="Hello"),
            {"email": TEST_EMAIL}, [], "https://example.com/x:streamQuery",
            {}, {}, _time.monotonic(), "run-abort")
        # Pull the first progress frame, then hang up mid-run.
        await stream.__anext__()
        await stream.aclose()

    asyncio.run(drive())

    assert proxy_access.get_record(TEST_EMAIL, cached=False)["tokens_used"] == 42
    runs = [d for p, d in fake_store.docs.items() if p[0] == proxy_access.RUNS_COLLECTION]
    assert [r["error_kind"] for r in runs] == ["aborted"]
    assert runs[0]["tokens_total"] == 42
