"""Fixtures for the Playwright UI tests.

Runs the proxy in mock mode (AGENT_ENGINE_ENDPOINT unset) so the whole UI is
exercisable offline: /analyze-prompt returns deterministic canned responses,
including a canned causal graph and an attachment acknowledgment.

The access gate stays *on* — disabling a security feature to keep tests green
would make the suite lie about what ships. Instead the server runs with an
in-memory access store and a pinned signing secret, and an autouse fixture
signs the browser in the way a real visitor would arrive: request access, get
approved, land with a session. Mark a test ``@pytest.mark.logged_out`` to
exercise the gate itself.

Setup: pip install -r requirements-dev.txt && playwright install chromium
Run:   python -m pytest tests/ui_tests -v
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

REPO_ROOT = Path(__file__).resolve().parents[2]
PORT = 8123
BASE_URL = f"http://127.0.0.1:{PORT}"

# Pinned so this process can mint a session the server will accept — the test
# holds the signing key exactly as the server does.
ACCESS_SECRET = "e2e-signing-secret-not-for-production"
ADMIN_PASSWORD = "e2e-admin-password"
E2E_EMAIL = "e2e-visitor@example.com"

# Third-party console noise that is not a defect of this UI (font/CDN hiccups
# in headless browsers).
CONSOLE_NOISE = (
    "gstatic", "fonts.googleapis", "googletagmanager",
)


def _post(path, payload):
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


@pytest.fixture(scope="session")
def ui_bundle():
    """Ensure the React bundle exists — the proxy serves ui/dist, not source.

    Fails loudly rather than letting the suite run against a stale or missing
    bundle, which would surface as a wall of confusing selector timeouts.
    """
    dist = REPO_ROOT / "ui" / "dist"
    if not (dist / "index.html").exists():
        raise RuntimeError(
            f"UI bundle missing at {dist}. Run: cd ui && npm ci && npm run build"
        )
    return dist


@pytest.fixture(scope="session")
def server(ui_bundle, tmp_path_factory):  # noqa: ARG001 — ordering dependency
    """Boot the proxy in mock mode and wait for /health.

    Output is captured rather than discarded: the console mail transport prints
    each message instead of sending it, so the log is this suite's inbox —
    which is how the sign-in-link test gets a real link.
    """
    env = {k: v for k, v in os.environ.items() if k != "AGENT_ENGINE_ENDPOINT"}
    # No cloud, no mail: access records live in memory and every email is
    # printed instead of sent, so the suite needs no credentials at all.
    env["ACCESS_STORE"] = "memory"
    env["ACCESS_SIGNING_SECRET"] = ACCESS_SECRET
    env["ADMIN_TOKEN"] = ADMIN_PASSWORD
    env["APP_URL"] = BASE_URL
    env["ACCESS_MAIL_TRANSPORT"] = "console"
    # Every real transport is cleared, not just Resend. send_email() checks
    # SMTP first and uses it whenever all four values are present, so inheriting
    # a developer's SMTP_* from their shell would quietly aim this suite at a
    # live mailbox — mailing a stranger at E2E_EMAIL on every run, and failing
    # the tests that expect to read the link back out of the log.
    for name in ("RESEND_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"):
        env.pop(name, None)

    log_path = tmp_path_factory.mktemp("proxy") / "server.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "proxy.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 20
        while True:
            try:
                with urllib.request.urlopen(f"{BASE_URL}/health", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                if time.time() > deadline:
                    raise RuntimeError("Mock proxy server did not start on port %s" % PORT)
                time.sleep(0.3)
        _server_log.append(log_path)
        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


# Set by the server fixture. Reached only through the wait_for_email fixture:
# pytest loads this conftest under a different module name than an `import`
# from a test file does, so module state must not be read across that seam.
_server_log: list = []


@pytest.fixture(scope="session")
def wait_for_email(server):  # noqa: ARG001 — the log only exists once it's running
    """Search the server's printed output — this suite's inbox.

    Under ACCESS_MAIL_TRANSPORT=console the notifier prints each message rather
    than sending it, so tests can pick real approve/sign-in links out of the log
    instead of forging their own.
    """
    import re

    log_path = _server_log[0]

    def wait(pattern, timeout=10.0):
        deadline = time.time() + timeout
        expression = re.compile(pattern)
        while True:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            match = expression.search(text)
            if match:
                return match
            if time.time() > deadline:
                raise AssertionError(f"no output matching {pattern!r} within {timeout}s")
            time.sleep(0.2)

    return wait


@pytest.fixture(scope="session")
def approved_session(server):  # noqa: ARG001 — needs the server running
    """Walk a visitor through the real approval flow and return their session.

    Request → approve via the same signed one-click link the notification email
    carries → mint the session token the sign-in link would have produced. The
    server validates all of it; nothing here bypasses the gate.
    """
    os.environ["ACCESS_SIGNING_SECRET"] = ACCESS_SECRET
    from proxy import access

    _post("/auth/login", {"email": E2E_EMAIL})

    approve = access.sign({"a": "approve", "email": E2E_EMAIL},
                          access.PURPOSE_ADMIN_ACT, access.ADMIN_ACT_TTL_S)
    with urllib.request.urlopen(f"{BASE_URL}/admin/act?t={approve}", timeout=5) as response:
        assert response.status == 200

    return access.sign({"email": E2E_EMAIL, "ver": 1},
                       access.PURPOSE_SESSION, access.SESSION_TTL_S)


@pytest.fixture(autouse=True)
def signed_in(request, page, approved_session):
    """Arrive already signed in, unless the test is about the gate itself."""
    if "logged_out" in request.keywords:
        return None
    page.add_init_script(
        "try {"
        f"  localStorage.setItem('tracerlens-session', {approved_session!r});"
        f"  localStorage.setItem('tracerlens-email', {E2E_EMAIL!r});"
        "} catch (e) {}"
    )
    return approved_session


@pytest.fixture
def console_errors(page):
    """Collect real console/page errors, filtering third-party noise."""
    errors = []

    def on_console(msg):
        if msg.type == "error":
            errors.append(msg.text)

    def on_pageerror(err):
        errors.append(str(err))

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)

    def significant():
        return [e for e in errors if not any(noise in e.lower() for noise in CONSOLE_NOISE)]

    yield significant


@pytest.fixture
def sample_txt(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("the sky is blue and grass is green", encoding="utf-8")
    return path


@pytest.fixture
def sample_exe(tmp_path):
    path = tmp_path / "virus.exe"
    path.write_bytes(b"MZ\x90\x00fakebinary")
    return path
