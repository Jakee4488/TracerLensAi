import json
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

import proxy.main as proxy_main

# ── Fake Firestore ───────────────────────────────────────────────────────────

class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return self._data

class FakeStore:
    """Flat dict of path-tuple → doc dict, mimicking Firestore's tree."""
    def __init__(self):
        self.docs = {}
        self._counter = 0

    def next_id(self):
        self._counter += 1
        return f"auto-{self._counter:04d}"

class FakeDocRef:
    def __init__(self, store, path):
        self.store = store
        self.path = path

    def collection(self, name):
        return FakeCollection(self.store, self.path + (name,))

    def get(self):
        return FakeSnapshot(self.path[-1], self.store.docs.get(self.path))

    def set(self, data, merge=False):
        if merge and self.path in self.store.docs:
            self.store.docs[self.path].update(data)
        else:
            self.store.docs[self.path] = dict(data)

    def update(self, data):
        doc = self.store.docs[self.path]
        for key, value in data.items():
            if type(value).__name__ == "Increment":
                doc[key] = doc.get(key, 0) + value.value
            else:
                doc[key] = value

class FakeCollection:
    def __init__(self, store, path):
        self.store = store
        self.path = path
        self._order_field = None
        self._descending = False
        self._limit = None
        self._start_after = None

    def document(self, doc_id):
        return FakeDocRef(self.store, self.path + (doc_id,))

    def add(self, data):
        doc_id = self.store.next_id()
        self.store.docs[self.path + (doc_id,)] = dict(data)

    def order_by(self, field, direction=None):
        self._order_field = field
        self._descending = direction is not None and "DESC" in str(direction).upper()
        return self

    def limit(self, n):
        self._limit = n
        return self

    def start_after(self, cursor):
        # Real Firestore takes a snapshot or a field-value dict; the proxy uses
        # the dict form keyed on the ordered field.
        self._start_after = cursor
        return self

    def stream(self):
        depth = len(self.path) + 1
        items = [
            (p[-1], d) for p, d in self.store.docs.items()
            if len(p) == depth and p[:-1] == self.path
        ]
        if self._order_field:
            items.sort(key=lambda kv: kv[1].get(self._order_field), reverse=self._descending)
        if self._start_after is not None and self._order_field:
            after = self._start_after[self._order_field]
            items = [
                (doc_id, data) for doc_id, data in items
                if (data.get(self._order_field) < after if self._descending
                    else data.get(self._order_field) > after)
            ]
        if self._limit is not None:
            items = items[:self._limit]
        return [FakeSnapshot(doc_id, data) for doc_id, data in items]

class FakeDb:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        return FakeCollection(self.store, (name,))

@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(proxy_main, "get_db", lambda: FakeDb(store))
    monkeypatch.setattr(proxy_main, "_get_firebase_app", lambda: None)
    monkeypatch.setattr(
        proxy_main.firebase_auth, "verify_id_token",
        lambda token: {"uid": "user-123", "email": "test@example.com", "name": "Test User"},
    )
    return store


AUTH = {"Authorization": "Bearer fake-valid-token"}

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

# ── Auth & history tests ─────────────────────────────────────────────────────

def test_history_requires_auth(client: TestClient):
    assert client.get("/history").status_code == 401
    assert client.get("/history/some-chat").status_code == 401

def test_history_rejects_invalid_token(client: TestClient, monkeypatch):
    monkeypatch.setattr(proxy_main, "_get_firebase_app", lambda: None)

    def raise_invalid(token):
        raise ValueError("bad token")
    monkeypatch.setattr(proxy_main.firebase_auth, "verify_id_token", raise_invalid)
    response = client.get("/history", headers={"Authorization": "Bearer forged"})
    assert response.status_code == 401

def test_analyze_prompt_authenticated_persists_history(client: TestClient, fake_store):
    # Mock engine path (no AGENT_ENGINE_ENDPOINT) with a signed-in user
    response = client.post("/analyze-prompt", json={
        "prompt": "What causes rain?",
        "chat_id": "chat-abc",
    }, headers=AUTH)
    assert response.status_code == 200

    # User profile upserted
    assert fake_store.docs[("users", "user-123")]["email"] == "test@example.com"
    # Conversation created with title from the prompt
    conv = fake_store.docs[("users", "user-123", "conversations", "chat-abc")]
    assert conv["title"] == "What causes rain?"
    assert conv["total_tokens"] == 10
    # Both messages stored
    msg_paths = [p for p in fake_store.docs
                 if len(p) == 6 and p[:5] == ("users", "user-123", "conversations", "chat-abc", "messages")]
    roles = sorted(fake_store.docs[p]["role"] for p in msg_paths)
    assert roles == ["ai", "user"]

def test_get_history_lists_conversations(client: TestClient, fake_store):
    client.post("/analyze-prompt", json={"prompt": "First chat", "chat_id": "chat-1"}, headers=AUTH)
    client.post("/analyze-prompt", json={"prompt": "Second chat", "chat_id": "chat-2"}, headers=AUTH)

    response = client.get("/history", headers=AUTH)
    assert response.status_code == 200
    conversations = response.json()["conversations"]
    assert len(conversations) == 2
    assert {c["chat_id"] for c in conversations} == {"chat-1", "chat-2"}
    assert conversations[0]["title"] in ("First chat", "Second chat")

def test_get_history_messages_and_404(client: TestClient, fake_store):
    client.post("/analyze-prompt", json={"prompt": "Hello there", "chat_id": "chat-1"}, headers=AUTH)

    response = client.get("/history/chat-1", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Hello there"
    assert [m["role"] for m in data["messages"]] == ["user", "ai"]
    assert data["messages"][0]["content"] == "Hello there"

    assert client.get("/history/nope", headers=AUTH).status_code == 404

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

def test_analyze_prompt_authenticated_passes_uid_to_engine(client: TestClient, fake_store, fake_engine):
    response = client.post("/analyze-prompt", json={"prompt": "Why rain?", "chat_id": "chat-9"}, headers=AUTH)
    assert response.status_code == 200
    assert fake_engine["payload"]["input"]["user_id"] == "user-123"
    # Real engine path also persists with the real token count
    conv = fake_store.docs[("users", "user-123", "conversations", "chat-9")]
    assert conv["total_tokens"] == 7

# ── Anonymous session isolation ──────────────────────────────────────────────

ANON_A = "11111111-2222-4333-8444-555555555555"
ANON_B = "99999999-8888-4777-8666-555555555555"

def test_distinct_anon_ids_get_distinct_agent_sessions(client: TestClient, fake_engine):
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers={"X-Anon-Id": ANON_A})
    first = fake_engine["payload"]["input"]["user_id"]
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers={"X-Anon-Id": ANON_B})
    second = fake_engine["payload"]["input"]["user_id"]

    assert first == f"anon:{ANON_A}"
    assert first != second
    # The shared bucket that leaked context between visitors is gone.
    assert "default-user" not in (first, second)

def test_same_anon_id_shares_one_agent_session(client: TestClient, fake_engine):
    client.post("/analyze-prompt", json={"prompt": "One", "chat_id": "c1"},
                headers={"X-Anon-Id": ANON_A})
    first = fake_engine["payload"]["input"]["user_id"]
    client.post("/analyze-prompt", json={"prompt": "Two", "chat_id": "c1"},
                headers={"X-Anon-Id": ANON_A})
    assert fake_engine["payload"]["input"]["user_id"] == first

def test_signed_in_request_ignores_anon_id(client: TestClient, fake_store, fake_engine):
    headers = dict(AUTH)
    headers["X-Anon-Id"] = ANON_A
    client.post("/analyze-prompt", json={"prompt": "Why rain?", "chat_id": "c1"}, headers=headers)
    assert fake_engine["payload"]["input"]["user_id"] == "user-123"

@pytest.mark.parametrize("bad", ["", "../../etc/passwd", "not-a-uuid", "x" * 500])
def test_malformed_anon_id_falls_back_to_unknown(client: TestClient, fake_engine, bad):
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers={"X-Anon-Id": bad})
    assert fake_engine["payload"]["input"]["user_id"] == "anon:unknown"

def test_missing_chat_id_gets_throwaway_session(client: TestClient, fake_engine):
    client.post("/analyze-prompt", json={"prompt": "Hi"}, headers={"X-Anon-Id": ANON_A})
    first = fake_engine["payload"]["input"]["session_id"]
    client.post("/analyze-prompt", json={"prompt": "Hi"}, headers={"X-Anon-Id": ANON_A})
    second = fake_engine["payload"]["input"]["session_id"]
    assert first != second
    assert "default-session" not in (first, second)

# ── Causal payload persistence ───────────────────────────────────────────────

def _ai_message(fake_store, chat_id):
    prefix = ("users", "user-123", "conversations", chat_id, "messages")
    docs = [d for p, d in fake_store.docs.items() if len(p) == 6 and p[:5] == prefix]
    return next(d for d in docs if d["role"] == "ai")

def test_causal_turn_persists_and_replays_payload(client: TestClient, fake_store):
    client.post("/analyze-prompt", json={
        "prompt": "Does price affect demand?",
        "chat_id": "chat-c",
        "causal_reasoning": True,
    }, headers=AUTH)

    stored = _ai_message(fake_store, "chat-c")["causal"]
    assert stored["causal_graph"]["nodes"]
    assert stored["causal_estimand"]["treatment"] == "price"

    # Round-trips through the API using the key names addAiMessage() reads.
    data = client.get("/history/chat-c", headers=AUTH).json()
    causal = data["messages"][1]["causal"]
    assert causal["causal_graph"] == stored["causal_graph"]
    assert causal["causal_effect"]["point"] == -1.42
    assert causal["causal_reasoning_steps"]

def test_non_causal_turn_omits_causal_key(client: TestClient, fake_store):
    client.post("/analyze-prompt", json={"prompt": "Hello", "chat_id": "chat-p"}, headers=AUTH)
    assert "causal" not in _ai_message(fake_store, "chat-p")
    data = client.get("/history/chat-p", headers=AUTH).json()
    assert "causal" not in data["messages"][1]

def test_history_write_failure_still_returns_200(client: TestClient, fake_store, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(proxy_main, "_save_exchange", boom)
    response = client.post("/analyze-prompt", json={
        "prompt": "Does price affect demand?",
        "chat_id": "chat-x",
        "causal_reasoning": True,
    }, headers=AUTH)
    # Persistence is best-effort: the turn still succeeds with its causal data.
    assert response.status_code == 200
    assert response.json()["causal_graph"]["nodes"]

# ── History pagination ───────────────────────────────────────────────────────

def test_history_paginates_with_cursor(client: TestClient, fake_store):
    for i in range(5):
        client.post("/analyze-prompt", json={"prompt": f"Chat {i}", "chat_id": f"c{i}"}, headers=AUTH)

    first = client.get("/history?limit=2", headers=AUTH).json()
    assert len(first["conversations"]) == 2
    assert first["next_cursor"]

    second = client.get(f"/history?limit=2&cursor={first['next_cursor']}", headers=AUTH).json()
    assert len(second["conversations"]) == 2
    # No overlap between pages.
    ids = {c["chat_id"] for c in first["conversations"]}
    assert ids.isdisjoint({c["chat_id"] for c in second["conversations"]})

    last = client.get(f"/history?limit=2&cursor={second['next_cursor']}", headers=AUTH).json()
    assert len(last["conversations"]) == 1
    assert last["next_cursor"] is None

    seen = ids | {c["chat_id"] for c in second["conversations"]} | {
        c["chat_id"] for c in last["conversations"]}
    assert seen == {f"c{i}" for i in range(5)}

def test_history_cursor_accepts_percent_encoded_offset(client: TestClient, fake_store):
    """The '+' in a UTC offset survives either encoding on the way back in."""
    for i in range(3):
        client.post("/analyze-prompt", json={"prompt": f"Chat {i}", "chat_id": f"c{i}"}, headers=AUTH)

    cursor = client.get("/history?limit=2", headers=AUTH).json()["next_cursor"]
    encoded = quote(cursor, safe="")
    assert client.get(f"/history?limit=2&cursor={encoded}", headers=AUTH).status_code == 200
    assert client.get(f"/history?limit=2&cursor={cursor}", headers=AUTH).status_code == 200

def test_history_malformed_cursor_400s(client: TestClient, fake_store):
    client.post("/analyze-prompt", json={"prompt": "Chat", "chat_id": "c0"}, headers=AUTH)
    assert client.get("/history?cursor=not-a-date", headers=AUTH).status_code == 400

# ── Upload & attachment tests ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_uploads():
    proxy_main._uploads.clear()
    yield
    proxy_main._uploads.clear()

def test_upload_txt_success(client: TestClient):
    response = client.post("/upload", files={"file": ("notes.txt", b"hello world", "text/plain")})
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "notes.txt"
    assert data["size"] == 11
    assert data["text_chars"] == 11
    assert data["file_id"] in proxy_main._uploads

def test_upload_rejects_oversize(client: TestClient):
    blob = b"x" * (proxy_main.MAX_UPLOAD_BYTES + 1)
    response = client.post("/upload", files={"file": ("big.txt", blob, "text/plain")})
    assert response.status_code == 413
    assert "limit" in response.json()["detail"]

def test_upload_rejects_bad_type(client: TestClient):
    response = client.post("/upload", files={"file": ("virus.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert response.status_code == 415
    assert ".exe" in response.json()["detail"]

def test_upload_sanitizes_path_traversal_filename(client: TestClient):
    response = client.post("/upload", files={"file": ("../../evil.md", b"# hi", "text/markdown")})
    assert response.status_code == 200
    assert response.json()["filename"] == "evil.md"

def test_analyze_prompt_with_attachment_mock_ack(client: TestClient):
    upload = client.post("/upload", files={"file": ("notes.txt", b"hello world", "text/plain")})
    file_id = upload.json()["file_id"]

    response = client.post("/analyze-prompt", json={"prompt": "Summarise", "attachments": [file_id]})
    assert response.status_code == 200
    assert "Attached files (1): notes.txt" in response.json()["response"]

def test_analyze_prompt_unknown_attachment_404(client: TestClient):
    response = client.post("/analyze-prompt", json={"prompt": "Hi", "attachments": ["nope"]})
    assert response.status_code == 404

def test_attachment_ownership_enforced(client: TestClient, fake_store):
    # Uploaded while signed in → not referencable anonymously (404, not 403).
    upload = client.post("/upload", files={"file": ("secret.txt", b"top secret", "text/plain")}, headers=AUTH)
    file_id = upload.json()["file_id"]

    anonymous = client.post("/analyze-prompt", json={"prompt": "Hi", "attachments": [file_id]})
    assert anonymous.status_code == 404

    owner = client.post("/analyze-prompt", json={"prompt": "Hi", "attachments": [file_id]}, headers=AUTH)
    assert owner.status_code == 200

def test_analyze_prompt_persists_attachment_names(client: TestClient, fake_store):
    upload = client.post("/upload", files={"file": ("data.csv", b"a,b\n1,2", "text/csv")}, headers=AUTH)
    file_id = upload.json()["file_id"]

    response = client.post("/analyze-prompt", json={
        "prompt": "Analyse this", "chat_id": "chat-att", "attachments": [file_id],
    }, headers=AUTH)
    assert response.status_code == 200

    msg_paths = [p for p in fake_store.docs
                 if len(p) == 6 and p[:5] == ("users", "user-123", "conversations", "chat-att", "messages")]
    user_msgs = [fake_store.docs[p] for p in msg_paths if fake_store.docs[p]["role"] == "user"]
    assert user_msgs[0]["attachments"] == ["data.csv"]

def test_analyze_prompt_real_engine_includes_file_context(client: TestClient, monkeypatch):
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

    upload = client.post("/upload", files={"file": ("notes.txt", b"the sky is blue", "text/plain")})
    file_id = upload.json()["file_id"]

    response = client.post("/analyze-prompt", json={
        "prompt": "Summarise the file",
        "causal_reasoning": True,
        "attachments": [file_id],
    })
    assert response.status_code == 200
    message = captured["payload"]["input"]["message"]
    # Causal marker must stay first for the agent's router, context before prompt.
    assert message.startswith(proxy_main.CAUSAL_MODE_MARKER)
    assert "--- Attached file: notes.txt ---" in message
    assert "the sky is blue" in message
    assert message.index("Attached file") < message.index("Summarise the file")
