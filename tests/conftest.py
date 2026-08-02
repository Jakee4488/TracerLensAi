import json

import pytest
from fastapi.testclient import TestClient

import proxy.access as proxy_access
import proxy.main as proxy_main
from proxy.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fast_mock_stream(monkeypatch):
    """Drop the offline mock stream's pacing so unit tests don't sleep.

    The 150ms/frame default exists so a human (and the Playwright suite) can
    watch stages land; it is pure cost here.
    """
    monkeypatch.setenv("MOCK_FRAME_DELAY_S", "0")


@pytest.fixture(autouse=True)
def _clean_access_state(monkeypatch):
    """Isolate the access layer between tests.

    Also unsets RESEND_API_KEY unconditionally: with a real key in the
    developer's environment, any test that triggers a notification would send
    actual email. The notifier prints instead when the key is absent.
    """
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    proxy_access._record_cache.clear()
    yield
    proxy_access._record_cache.clear()


# ── Fake Firestore ───────────────────────────────────────────────────────────
# A flat dict standing in for Firestore's document tree. Shared by every suite,
# so it has to cover what the access/admin code needs (deletes, ordered scans)
# as well as the history paths it was originally written for.

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

    def delete(self):
        self.store.docs.pop(self.path, None)


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
    """Swap Firestore for the fake.

    Both modules need patching: proxy/access.py owns ``get_db`` and calls it
    for the access records, while proxy/main.py holds an imported reference it
    uses for conversation history.
    """
    store = FakeStore()
    monkeypatch.setattr(proxy_access, "get_db", lambda: FakeDb(store))
    monkeypatch.setattr(proxy_main, "get_db", lambda: FakeDb(store))
    monkeypatch.setattr(proxy_access, "get_firebase_app", lambda: None)
    return store


@pytest.fixture
def no_email(monkeypatch):
    """Capture outbound email instead of sending (or printing) it."""
    sent = []

    async def _capture(to, subject, text, html=None):
        sent.append({"to": to, "subject": subject, "text": text})
        return True, ""

    monkeypatch.setattr(proxy_access, "send_email", _capture)
    return sent


# ── Access helpers ───────────────────────────────────────────────────────────

TEST_EMAIL = "visitor@example.com"


def approve_email(email=TEST_EMAIL, **overrides):
    """Create an approved access record and return it."""
    proxy_access.create_or_touch_request(email)
    proxy_access.set_status(email, "approved")
    if overrides:
        proxy_access.update_record(email, overrides)
    return proxy_access.get_record(email, cached=False)


def session_headers(email=TEST_EMAIL):
    """Authorization header for a live session belonging to ``email``."""
    record = proxy_access.get_record(email, cached=False) or {}
    return {"Authorization": "Bearer " + proxy_access.mint_session(email, record)}


@pytest.fixture
def approved(fake_store):
    """An approved visitor plus the headers to act as them."""
    approve_email()
    return session_headers()


# ── SSE helpers ──────────────────────────────────────────────────────────────
# /analyze-prompt streams Server-Sent Events. Tests assert on the frames rather
# than a JSON body.

def sse_frames(response):
    """Parse an SSE response body into a list of (event_name, payload)."""
    frames = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:  # skip ": ping" keepalive comments
            frames.append((name, json.loads("\n".join(data_lines))))
    return frames


def sse_report(response):
    """Return the terminal `done` payload — the report body of a run."""
    for name, payload in sse_frames(response):
        if name == "done":
            return payload
    raise AssertionError(
        f"no `done` frame in stream; frames were "
        f"{[n for n, _ in sse_frames(response)]}")
