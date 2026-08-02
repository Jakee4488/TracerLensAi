"""The email gate: login, approval, quota, extensions, and erasure."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import proxy.access as access
from tests.conftest import (
    TEST_EMAIL, approve_email, session_headers, sse_report,
)

USER_KEY = access.email_key(TEST_EMAIL)

CHAT = {"prompt": "Hello", "chat_id": "chat-1"}


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch):
    """Pin the signing key so tokens are stable within a test."""
    monkeypatch.setenv("ACCESS_SIGNING_SECRET", "test-secret-do-not-use")


# ── Login ────────────────────────────────────────────────────────────────────

def test_login_unknown_email_creates_pending_and_notifies(client: TestClient, fake_store, no_email):
    response = client.post("/auth/login", json={"email": "New.Visitor@Example.COM "})
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    # Address is normalized before anything is keyed on it.
    record = access.get_record("new.visitor@example.com", cached=False)
    assert record["status"] == "pending"
    assert record["email"] == "new.visitor@example.com"
    assert record["token_limit"] == 200000
    assert record["tokens_used"] == 0

    assert len(no_email) == 1
    assert no_email[0]["to"] == access.notify_email()
    assert "new.visitor@example.com" in no_email[0]["text"]


@pytest.mark.parametrize("bad", ["", "   ", "not-an-email", "a@b", "@example.com", "x" * 300])
def test_login_rejects_malformed_email(client: TestClient, fake_store, no_email, bad):
    assert client.post("/auth/login", json={"email": bad}).status_code == 400
    assert no_email == []


def test_login_resubmit_does_not_re_notify(client: TestClient, fake_store, no_email):
    """An impatient visitor must not turn into five identical emails."""
    for _ in range(4):
        client.post("/auth/login", json={"email": TEST_EMAIL})
    assert len(no_email) == 1


def test_login_re_notifies_once_the_cooldown_lapses(client: TestClient, fake_store, no_email,
                                                    monkeypatch):
    client.post("/auth/login", json={"email": TEST_EMAIL})
    monkeypatch.setattr(access, "NOTIFY_COOLDOWN_S", -1)
    client.post("/auth/login", json={"email": TEST_EMAIL})
    assert len(no_email) == 2


def test_login_approved_email_sends_a_sign_in_link(client: TestClient, fake_store, no_email):
    approve_email()
    no_email.clear()

    response = client.post("/auth/login", json={"email": TEST_EMAIL})
    assert response.json()["status"] == "link_sent"
    assert no_email[-1]["to"] == TEST_EMAIL
    assert "?auth=" in no_email[-1]["text"]


def test_login_denied_email_reports_denied(client: TestClient, fake_store, no_email):
    access.create_or_touch_request(TEST_EMAIL)
    access.set_status(TEST_EMAIL, "denied")
    no_email.clear()

    assert client.post("/auth/login", json={"email": TEST_EMAIL}).json()["status"] == "denied"
    # A denied address must not keep pinging the admin.
    assert no_email == []


# ── Exchanging a login link ──────────────────────────────────────────────────

def _login_token(email=TEST_EMAIL):
    nonce = access.issue_login_nonce(email)
    return access.sign({"email": email, "n": nonce}, access.PURPOSE_LOGIN,
                       access.LOGIN_LINK_TTL_S)


def test_exchange_returns_a_working_session(client: TestClient, fake_store, no_email):
    approve_email()
    response = client.post("/auth/exchange", json={"auth": _login_token()})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["token_limit"] == 200000

    headers = {"Authorization": "Bearer " + body["token"]}
    assert client.post("/analyze-prompt", json=CHAT, headers=headers).status_code == 200


def test_login_link_works_exactly_once(client: TestClient, fake_store, no_email):
    approve_email()
    token = _login_token()
    assert client.post("/auth/exchange", json={"auth": token}).status_code == 200
    # A forwarded or leaked link is dead after the first use.
    assert client.post("/auth/exchange", json={"auth": token}).status_code == 401


def test_expired_login_link_is_rejected(client: TestClient, fake_store, no_email):
    approve_email()
    nonce = access.issue_login_nonce(TEST_EMAIL)
    stale = access.sign({"email": TEST_EMAIL, "n": nonce}, access.PURPOSE_LOGIN, -1)
    assert client.post("/auth/exchange", json={"auth": stale}).status_code == 401


def test_tampered_login_link_is_rejected(client: TestClient, fake_store, no_email):
    approve_email()
    token = _login_token()
    payload, _, signature = token.rpartition(".")
    forged = access._b64e(json.dumps(
        {"email": "attacker@example.com", "n": "x", "p": "login",
         "exp": 2 ** 31}).encode()) + "." + signature
    assert client.post("/auth/exchange", json={"auth": forged}).status_code == 401


def test_login_link_cannot_be_used_as_a_session(client: TestClient, fake_store, no_email):
    """Purpose is signed in, so one kind of token can't stand in for another."""
    approve_email()
    token = _login_token()
    response = client.post("/analyze-prompt", json=CHAT,
                           headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401


def test_exchange_before_approval_is_refused(client: TestClient, fake_store, no_email):
    access.create_or_touch_request(TEST_EMAIL)
    response = client.post("/auth/exchange", json={"auth": _login_token()})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "pending"


# ── Session revocation ───────────────────────────────────────────────────────

def test_denial_revokes_live_sessions(client: TestClient, fake_store, no_email):
    approve_email()
    headers = session_headers()
    assert client.post("/analyze-prompt", json=CHAT, headers=headers).status_code == 200

    access.set_status(TEST_EMAIL, "denied")
    # Not merely 403 — the token itself stops verifying.
    assert client.post("/analyze-prompt", json=CHAT, headers=headers).status_code == 401


def test_deletion_revokes_live_sessions(client: TestClient, fake_store, no_email):
    approve_email()
    headers = session_headers()
    access.delete_user(TEST_EMAIL)
    assert client.get("/history", headers=headers).status_code == 401


# ── The gate ─────────────────────────────────────────────────────────────────

def test_agent_is_closed_without_a_session(client: TestClient, fake_store):
    response = client.post("/analyze-prompt", json=CHAT)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "no_session"


def test_upload_is_closed_without_a_session(client: TestClient, fake_store):
    """Otherwise the upload endpoint is an open file sink."""
    response = client.post("/upload", files={"file": ("n.txt", b"hi", "text/plain")})
    assert response.status_code == 403


def test_pending_user_is_blocked(client: TestClient, fake_store, no_email):
    access.create_or_touch_request(TEST_EMAIL)
    response = client.post("/analyze-prompt", json=CHAT, headers=session_headers())
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "pending"
    assert "approval" in detail["message"]


def test_user_at_the_cap_is_blocked(client: TestClient, fake_store, no_email):
    approve_email(tokens_used=200000, token_limit=200000)
    response = client.post("/analyze-prompt", json=CHAT, headers=session_headers())
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "limit_reached"
    assert detail["usage"] == 200000 and detail["limit"] == 200000


def test_user_just_under_the_cap_is_allowed(client: TestClient, fake_store, no_email):
    approve_email(tokens_used=199999, token_limit=200000)
    assert client.post("/analyze-prompt", json=CHAT,
                       headers=session_headers()).status_code == 200


# ── Usage accounting ─────────────────────────────────────────────────────────

def test_tokens_accumulate_across_turns(client: TestClient, fake_store, approved):
    for _ in range(3):
        client.post("/analyze-prompt", json=CHAT, headers=approved)
    # The offline mock reports 10 tokens a turn.
    assert access.get_record(TEST_EMAIL, cached=False)["tokens_used"] == 30


def test_usage_counts_real_token_totals(client: TestClient, fake_store, approved, monkeypatch):
    monkeypatch.setenv("AGENT_ENGINE_ENDPOINT", "https://example.com/v1/reasoningEngines/1:query")

    class DummyCredentials:
        token = "fake-token"

        def refresh(self, request):
            pass

    monkeypatch.setattr("google.auth.default", lambda scopes: (DummyCredentials(), "p"))
    body = json.dumps({
        "content": {"parts": [{"text": "Hi"}]},
        "usage_metadata": {"total_token_count": 137, "prompt_token_count": 100,
                           "candidates_token_count": 37},
    }).encode("utf-8")
    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(body))

    client.post("/analyze-prompt", json=CHAT, headers=approved)
    assert access.get_record(TEST_EMAIL, cached=False)["tokens_used"] == 137

    run = _only_run(fake_store)
    assert run["tokens_total"] == 137
    assert run["tokens_in"] == 100 and run["tokens_out"] == 37
    assert run["ok"] is True
    # Metrics must never carry chat content — that would outlive the 24h promise.
    assert "prompt" not in run and "response" not in run


def test_failed_turn_is_recorded_with_its_error_kind(client: TestClient, fake_store, approved,
                                                     monkeypatch):
    monkeypatch.setenv("AGENT_ENGINE_ENDPOINT", "https://example.com/v1/reasoningEngines/1:query")

    class DummyCredentials:
        token = "fake-token"

        def refresh(self, request):
            pass

    monkeypatch.setattr("google.auth.default", lambda scopes: (DummyCredentials(), "p"))
    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(b"upstream exploded", status=500))

    client.post("/analyze-prompt", json=CHAT, headers=approved)

    run = _only_run(fake_store)
    assert run["ok"] is False
    assert run["error_kind"] == "upstream_http"
    assert access.get_record(TEST_EMAIL, cached=False)["runs_failed"] == 1


def test_run_aggregates_feed_the_dashboard(client: TestClient, fake_store, approved):
    client.post("/analyze-prompt", json=CHAT, headers=approved)
    record = access.get_record(TEST_EMAIL, cached=False)
    assert record["runs_total"] == 1
    assert record["runs_failed"] == 0
    assert record["latency_ms_sum"] >= 0
    assert record["last_run_at"] is not None


def _only_run(fake_store):
    runs = [d for p, d in fake_store.docs.items() if p[0] == access.RUNS_COLLECTION]
    assert len(runs) == 1, f"expected one run row, got {len(runs)}"
    return runs[0]


def _stub_client(body: bytes, status: int = 200):
    class DummyStreamResponse:
        status_code = status

        async def aread(self):
            return body

        async def aiter_lines(self):
            for line in body.decode("utf-8").splitlines():
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

    return DummyAsyncClient


# ── Extensions ───────────────────────────────────────────────────────────────

def test_extension_request_notifies_the_admin(client: TestClient, fake_store, no_email):
    approve_email(tokens_used=200000, token_limit=200000)
    no_email.clear()

    response = client.post("/access/extension", json={"message": "Writing a blog post about it"},
                           headers=session_headers())
    assert response.status_code == 200
    assert response.json()["extension_status"] == "pending"

    assert no_email[-1]["to"] == access.notify_email()
    assert "Writing a blog post" in no_email[-1]["text"]
    assert TEST_EMAIL in no_email[-1]["text"]


def test_user_stays_blocked_until_the_extension_is_granted(client: TestClient, fake_store, no_email):
    approve_email(tokens_used=200000, token_limit=200000)
    client.post("/access/extension", json={"message": "please"}, headers=session_headers())

    blocked = client.post("/analyze-prompt", json=CHAT, headers=session_headers())
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "limit_reached"

    access.grant_extension(TEST_EMAIL)
    assert client.post("/analyze-prompt", json=CHAT,
                       headers=session_headers()).status_code == 200


def test_grant_adds_to_the_cap_instead_of_resetting_it(client: TestClient, fake_store, no_email):
    approve_email(tokens_used=250000, token_limit=400000)

    record = access.grant_extension(TEST_EMAIL)
    assert record["token_limit"] == 600000    # 400k + 200k, not 200k
    assert record["tokens_used"] == 250000    # usage survives the grant
    assert record["extension_status"] == "none"


def test_repeated_grants_keep_stacking(client: TestClient, fake_store, no_email):
    approve_email()
    access.grant_extension(TEST_EMAIL)
    record = access.grant_extension(TEST_EMAIL)
    assert record["token_limit"] == 600000


# ── Status polling ───────────────────────────────────────────────────────────

def test_status_without_a_session_is_logged_out(client: TestClient, fake_store):
    assert client.get("/access/status").json() == {"status": "logged_out"}


def test_status_reports_quota(client: TestClient, fake_store, no_email):
    approve_email(tokens_used=1234, token_limit=200000)
    body = client.get("/access/status", headers=session_headers()).json()
    assert body == {"status": "ok", "email": TEST_EMAIL, "tokens_used": 1234,
                    "token_limit": 200000, "extension_status": "none"}


def test_status_poll_retries_a_failed_notification(client: TestClient, fake_store, monkeypatch):
    """A Resend blip must not leave someone waiting on an email that never comes."""
    outcomes = iter([(False, "resend 500"), (True, "")])
    sent = []

    async def flaky(to, subject, text, html=None):
        sent.append(to)
        return next(outcomes)

    monkeypatch.setattr(access, "send_email", flaky)

    client.post("/auth/login", json={"email": TEST_EMAIL})
    assert access.get_record(TEST_EMAIL, cached=False)["notify_state"] == "failed"

    client.get("/access/status", headers=session_headers())
    assert access.get_record(TEST_EMAIL, cached=False)["notify_state"] == "sent"
    assert len(sent) == 2


def test_notification_retries_are_bounded(client: TestClient, fake_store, monkeypatch):
    async def always_fails(to, subject, text, html=None):
        return False, "resend 500"

    monkeypatch.setattr(access, "send_email", always_fails)
    client.post("/auth/login", json={"email": TEST_EMAIL})

    for _ in range(10):
        client.get("/access/status", headers=session_headers())
    assert (access.get_record(TEST_EMAIL, cached=False)["notify_attempts"]
            <= access.NOTIFY_MAX_ATTEMPTS)


# ── Erasure ──────────────────────────────────────────────────────────────────

def test_delete_account_removes_record_and_conversations(client: TestClient, fake_store, approved):
    client.post("/analyze-prompt", json=CHAT, headers=approved)
    assert any(p[:2] == ("users", USER_KEY) for p in fake_store.docs)

    assert client.delete("/account", headers=approved).status_code == 200

    assert access.get_record(TEST_EMAIL, cached=False) is None
    assert not [p for p in fake_store.docs if p[:2] == ("users", USER_KEY)]


def test_delete_account_requires_a_session(client: TestClient, fake_store):
    assert client.delete("/account").status_code == 401


def test_deleted_user_starts_over_as_pending(client: TestClient, fake_store, approved, no_email):
    client.delete("/account", headers=approved)
    assert client.post("/auth/login", json={"email": TEST_EMAIL}).json()["status"] == "pending"


# ── Privacy invariants ───────────────────────────────────────────────────────

def test_access_record_holds_only_what_the_notice_promises(client: TestClient, fake_store, no_email):
    """Data minimisation is a claim in the modal; keep it verifiable."""
    client.post("/auth/login", json={"email": TEST_EMAIL})
    record = access.get_record(TEST_EMAIL, cached=False)

    banned = {"ip", "ip_address", "user_agent", "prompt", "prompts", "response",
              "messages", "location", "referrer"}
    assert banned.isdisjoint(record.keys())


def test_mock_turn_reports_the_token_split(client: TestClient, fake_store, approved):
    report = sse_report(client.post("/analyze-prompt", json=CHAT, headers=approved))
    assert report["total_token_count"] == 10
