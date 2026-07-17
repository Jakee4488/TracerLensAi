import json

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
        return self

    def stream(self):
        depth = len(self.path) + 1
        items = [
            (p[-1], d) for p, d in self.store.docs.items()
            if len(p) == depth and p[:-1] == self.path
        ]
        if self._order_field:
            items.sort(key=lambda kv: kv[1].get(self._order_field), reverse=self._descending)
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

def test_analyze_prompt_authenticated_passes_uid_to_engine(client: TestClient, fake_store, monkeypatch):
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

    response = client.post("/analyze-prompt", json={"prompt": "Why rain?", "chat_id": "chat-9"}, headers=AUTH)
    assert response.status_code == 200
    assert captured["payload"]["input"]["user_id"] == "user-123"
    # Real engine path also persists with the real token count
    conv = fake_store.docs[("users", "user-123", "conversations", "chat-9")]
    assert conv["total_tokens"] == 7

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
