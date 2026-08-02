"""E2E coverage for the email access gate.

The rest of the suite runs signed in (see the ``signed_in`` autouse fixture in
conftest). These tests use ``@pytest.mark.logged_out`` to opt out and drive the
gate itself, against the same mock-mode proxy — no credentials, no network.

With RESEND_API_KEY unset the notifier prints every message to stdout, which
the server fixture captures. That log is this suite's inbox, so the sign-in
flow is exercised with a real link the server really issued.

Quota *enforcement* is covered exhaustively in tests/test_access.py; what
belongs here is the UI's reaction to it.
"""
import json
import urllib.error
import urllib.request

import pytest
from playwright.sync_api import Page, expect

from tests.ui_tests.conftest import BASE_URL, E2E_EMAIL

pytestmark = pytest.mark.logged_out


def _api(path, payload=None, method="POST", token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def _sign_in_as(page: Page, session: str, email: str):
    page.add_init_script(
        "try {"
        f"  localStorage.setItem('tracerlens-session', {session!r});"
        f"  localStorage.setItem('tracerlens-email', {email!r});"
        "} catch (e) {}"
    )


# ── The gate holds ───────────────────────────────────────────────────────────

def test_composer_is_locked_until_you_sign_in(page: Page, server):
    page.goto(server)
    expect(page.locator("#access-modal")).to_be_visible()
    expect(page.locator("#chat-input")).to_be_disabled()
    expect(page.locator("#send-btn")).to_be_disabled()
    expect(page.locator("#sign-in-btn")).to_have_text("Login")


def test_gate_cannot_be_dismissed(page: Page, server):
    """It's a gate, not a nag: Escape and backdrop clicks must not open it up."""
    page.goto(server)
    page.keyboard.press("Escape")
    page.locator(".access-backdrop").click(position={"x": 5, "y": 5})
    expect(page.locator("#access-modal")).to_be_visible()
    expect(page.locator("#chat-input")).to_be_disabled()


@pytest.mark.parametrize("path", ["/analyze-prompt", "/upload"])
def test_backend_refuses_signed_out_callers(server, path):
    """Enforced server-side, not merely hidden in the UI."""
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=b"{}",
        headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)
    # 422 on /upload because the multipart body is missing — either way, the
    # request never reaches the agent.
    assert caught.value.code in (403, 422)


# ── Requesting access ────────────────────────────────────────────────────────

def test_privacy_notice_states_what_is_collected(page: Page, server):
    """The notice is a promise the code has to keep; assert it is actually shown."""
    page.goto(server)
    page.locator(".access-privacy summary").click()
    notice = page.locator(".access-privacy")
    expect(notice).to_contain_text("email address")
    expect(notice).to_contain_text("token-usage counter")
    expect(notice).to_contain_text("within 24 hours")
    expect(notice).to_contain_text("jacobbinu4488code@gmail.com")


def test_submitting_an_email_shows_waiting_for_approval(page: Page, server, wait_for_email):
    visitor = "gate-tester@example.com"
    page.goto(server)
    page.fill("#access-email", visitor)
    page.click("#access-submit")

    expect(page.locator("#access-status")).to_contain_text("Waiting for approval")
    expect(page.locator("#access-status")).to_contain_text(visitor)
    # Submitting a request is not the same as being let in.
    expect(page.locator("#chat-input")).to_be_disabled()
    # And the admin was told, with a one-click approve link.
    wait_for_email(rf"Email: {visitor}")
    wait_for_email(r"Approve: http://\S+/admin/act\?t=\S+")


def test_approval_link_signs_you_in_and_is_stripped_from_the_url(page: Page, server, wait_for_email):
    """The whole arrival path, driven by links the server actually issued."""
    visitor = "link-tester@example.com"
    page.goto(server)
    page.fill("#access-email", visitor)
    page.click("#access-submit")

    approve_url = wait_for_email(
        rf"(?s)Email: {visitor}.*?Approve: (http://\S+)").group(1)
    urllib.request.urlopen(approve_url, timeout=5).close()

    # Anchored on the address: the log is shared by every test in this session.
    sign_in_url = wait_for_email(rf"(?s)To: {visitor}.*?(http://\S+/\?auth=\S+)").group(1)
    page.goto(sign_in_url)

    expect(page.locator("#chat-input")).to_be_enabled()
    expect(page.locator("#access-modal")).to_have_count(0)
    expect(page.locator("#profile-btn")).to_contain_text(visitor)
    # The single-use token must not linger in the address bar or back history.
    assert "auth=" not in page.url


def test_a_sign_in_link_works_only_once(page: Page, server, wait_for_email):
    visitor = "replay-tester@example.com"
    _api("/auth/login", {"email": visitor})
    approve_url = wait_for_email(
        rf"(?s)Email: {visitor}.*?Approve: (http://\S+)").group(1)
    urllib.request.urlopen(approve_url, timeout=5).close()
    sign_in_url = wait_for_email(rf"(?s)To: {visitor}.*?(http://\S+/\?auth=\S+)").group(1)

    page.goto(sign_in_url)
    expect(page.locator("#chat-input")).to_be_enabled()

    # A forwarded link is dead: the second visit lands back at the gate.
    page.evaluate("() => localStorage.clear()")
    page.goto(sign_in_url)
    expect(page.locator("#access-modal")).to_be_visible()


def test_denied_visitor_is_told_plainly(page: Page, server, wait_for_email):
    visitor = "denied-tester@example.com"
    _api("/auth/login", {"email": visitor})
    deny_url = wait_for_email(rf"(?s)Email: {visitor}.*?Deny:\s+(http://\S+)").group(1)
    urllib.request.urlopen(deny_url, timeout=5).close()

    page.goto(server)
    page.fill("#access-email", visitor)
    page.click("#access-submit")
    expect(page.locator("#access-status")).to_contain_text("Access not granted")
    expect(page.locator("#chat-input")).to_be_disabled()


# ── Signed in ────────────────────────────────────────────────────────────────

def test_profile_menu_shows_remaining_tokens_and_erasure(page: Page, server,
                                                         approved_session):
    _sign_in_as(page, approved_session, E2E_EMAIL)
    page.goto(server)
    page.click("#profile-btn")

    expect(page.locator("#profile-menu")).to_be_visible()
    expect(page.locator("#tokens-remaining")).to_be_visible()
    expect(page.locator("#profile-menu")).to_contain_text("Delete my data")
    expect(page.locator("#profile-menu")).to_contain_text("within 24 hours")


def test_requesting_more_tokens_notifies_the_admin(page: Page, server, approved_session,
                                                   wait_for_email):
    _sign_in_as(page, approved_session, E2E_EMAIL)
    page.goto(server)
    page.click("#profile-btn")
    page.click("text=Request more tokens")

    page.fill("#extension-message", "Comparing causal tooling for a write-up")
    page.click("#extension-submit")

    expect(page.locator("#access-status")).to_contain_text("Request sent")
    wait_for_email(r"Comparing causal tooling for a write-up")
    wait_for_email(r"Grant \+200,000: http://\S+")


def test_logging_out_re_locks_the_composer(page: Page, server, approved_session):
    _sign_in_as(page, approved_session, E2E_EMAIL)
    page.goto(server)
    expect(page.locator("#chat-input")).to_be_enabled()

    page.click("#profile-btn")
    page.click("text=Log out")
    expect(page.locator("#access-modal")).to_be_visible()
    expect(page.locator("#chat-input")).to_be_disabled()


# ── Admin dashboard ──────────────────────────────────────────────────────────

def test_admin_dashboard_demands_the_password(page: Page, server):
    page.goto(f"{server}/admin")
    expect(page.locator("#login")).to_be_visible()
    expect(page.locator("#app")).to_be_hidden()

    page.fill("#pw", "wrong-password")
    page.click("text=Send me a code")
    expect(page.locator("#err")).to_contain_text("Invalid credentials")


def test_admin_dashboard_requires_the_emailed_code(page: Page, server):
    """Password alone must not be enough — that is the point of the OTP."""
    from tests.ui_tests.conftest import ADMIN_PASSWORD

    page.goto(f"{server}/admin")
    page.fill("#pw", ADMIN_PASSWORD)
    page.click("text=Send me a code")

    expect(page.locator("#step2")).to_be_visible()
    expect(page.locator("#app")).to_be_hidden()

    page.fill("#code", "000000")
    page.click("text=Verify")
    expect(page.locator("#err")).to_contain_text("Invalid or expired code")
    expect(page.locator("#app")).to_be_hidden()
