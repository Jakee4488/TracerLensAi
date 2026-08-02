"""Fixtures for the Playwright UI tests.

Runs the proxy in mock mode (AGENT_ENGINE_ENDPOINT unset) so the whole UI is
exercisable offline: /analyze-prompt returns deterministic canned responses,
including a canned causal graph and an attachment acknowledgment.

Setup: pip install -r requirements-dev.txt && playwright install chromium
Run:   python -m pytest tests/ui_tests -v
"""
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

# Third-party console noise that is not a defect of this UI (Firebase
# analytics/auth probes and font/CDN hiccups in headless browsers).
CONSOLE_NOISE = (
    "gstatic", "firebase", "analytics", "installations",
    "fonts.googleapis", "googletagmanager",
)


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
def server(ui_bundle):  # noqa: ARG001 — ordering dependency, not a value
    """Boot the proxy in mock mode and wait for /health."""
    env = {k: v for k, v in os.environ.items() if k != "AGENT_ENGINE_ENDPOINT"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "proxy.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


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
