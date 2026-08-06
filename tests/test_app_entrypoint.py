"""Guards against a class of bug CI otherwise never sees: `src/fast_api_app.py`
is the actual production ASGI entrypoint (what `uvicorn` runs in the deployed
container), but nothing else in this suite imports it — `src/__init__.py`
deliberately lazy-loads it via PEP 562 specifically because it "requires GCP
credentials at import time", so every other test sticks to the credential-free
`src.agent` / `src.causal` surface instead.

That gap is exactly how a floating dependency broke production silently:
`a2a-sdk>=0.3.22` (no upper bound) resolved to a breaking 1.x release during
`agents-cli deploy`'s own fresh dependency resolve — CI's `pip install -r
requirements.txt` is just as unpinned and would have hit the same version, but
no test exercised the import chain that actually broke
(`fast_api_app -> app_utils.a2a -> a2a.server.apps.A2AFastAPIApplication`), so
it shipped clean through CI and only crash-looped once deployed. See the
`a2a-sdk` pin comment in pyproject.toml for the incident this test is for.

Credentials are stubbed, not real: this only proves the module *imports* and
the FastAPI app object *builds* — the same guarantee a fresh dependency
resolve breaking an import chain would violate — without needing GCP access
in CI (this workflow intentionally never grants PR runs cloud credentials).
"""

import sys

import pytest


@pytest.fixture
def import_entrypoint(monkeypatch):
    """Import src.fast_api_app under stubbed GCP credentials, guaranteed fresh.

    Returns a callable rather than the module, because the deployment guard
    runs at import time: a test has to set the environment it wants *before*
    the import happens, which a fixture that has already imported cannot offer.

    Purges any cached copy first: import caching means a stale, already-broken
    (or already-patched) module object would otherwise make this test
    order-dependent on whatever ran earlier in the same pytest session.
    """
    import google.auth
    import google.cloud.logging

    class _StubLogger:
        def log_struct(self, *a, **k):
            pass

    class _StubLoggingClient:
        def logger(self, name):
            return _StubLogger()

    monkeypatch.setattr(google.auth, "default", lambda *a, **k: (object(), "test-project"))
    monkeypatch.setattr(google.cloud.logging, "Client", lambda *a, **k: _StubLoggingClient())

    # The guard keys off the deployment environment, and a developer's shell or
    # .env may carry any of these. Clear them so each test states its own
    # situation outright instead of inheriting one.
    for name in ("K_SERVICE", "GAE_ENV", "FUNCTION_TARGET",
                 "ACCESS_SIGNING_SECRET", "ADMIN_TOKEN",
                 "ALLOW_AGENT_ON_PROXY_SERVICE"):
        monkeypatch.delenv(name, raising=False)

    def _import():
        for name in list(sys.modules):
            if name == "src.fast_api_app" or name.startswith("src.fast_api_app."):
                del sys.modules[name]

        import src.fast_api_app as fast_api_app
        return fast_api_app

    return _import


@pytest.fixture
def fresh_fast_api_app(import_entrypoint):
    """The module itself, for tests that do not care about the environment."""
    return import_entrypoint()


def test_production_entrypoint_imports_cleanly(fresh_fast_api_app):
    """The exact failure surface from the incident: a ModuleNotFoundError deep
    in this import chain (a2a-sdk floating to 1.x) crash-looped the deployed
    container, while `src.agent` alone — everything the rest of this suite
    touches — imported fine."""
    assert fresh_fast_api_app.app is not None


def test_production_entrypoint_builds_expected_routes(fresh_fast_api_app):
    """Not just "didn't crash": the module ran to completion, so the routes it
    defines at import time (attach_reasoning_engine_routes, the /feedback
    endpoint below it) actually got attached."""
    paths = {getattr(route, "path", None) for route in fresh_fast_api_app.app.routes}
    assert "/feedback" in paths


def test_production_entrypoint_wires_a2a_lifespan(fresh_fast_api_app):
    """The specific symbol that broke: attach_a2a_routes, imported from
    app_utils.a2a, which imports a2a.server.apps.A2AFastAPIApplication."""
    assert fresh_fast_api_app.app.router.lifespan_context is not None


# ── Wrong-image deployment guard ──────────────────────────────────────────────
# A `gcloud run deploy --source .` builds the root Dockerfile — this agent —
# and will ship it to whichever service it is aimed at. Pointed at the site's
# service it answers /health, passes the startup probe, takes all the traffic,
# and 404s every route the site actually has. That happened; these pin the
# guard that turns it into a failed revision instead.


def test_agent_refuses_to_boot_on_the_proxy_service(import_entrypoint, monkeypatch):
    """The real incident: the agent image landed on the site's service."""
    monkeypatch.setenv("K_SERVICE", "tracerlensai-app")
    monkeypatch.setenv("ACCESS_SIGNING_SECRET", "proxy-only-secret")

    with pytest.raises(Exception) as excinfo:
        import_entrypoint()
    # Named so the failure in the deploy log says what to do, not just "boom".
    assert type(excinfo.value).__name__ == "WrongServiceDeployment"
    assert "Dockerfile.proxy" in str(excinfo.value)


def test_admin_token_alone_is_enough_to_refuse(import_entrypoint, monkeypatch):
    """ADMIN_TOKEN is just as proxy-only; a partially configured service is
    still the wrong service, and is the more confusing way to find out."""
    monkeypatch.setenv("K_SERVICE", "tracerlensai-app")
    monkeypatch.setenv("ADMIN_TOKEN", "proxy-only-password")

    with pytest.raises(Exception) as excinfo:
        import_entrypoint()
    assert type(excinfo.value).__name__ == "WrongServiceDeployment"


def test_agent_boots_normally_on_its_own_service(import_entrypoint, monkeypatch):
    """The guard must not fire on a correct deploy — it sits in the import path
    of every agent revision, so a false positive here is its own outage."""
    monkeypatch.setenv("K_SERVICE", "tracerlensai-agent")

    assert import_entrypoint().app is not None


def test_proxy_env_locally_is_not_a_wrong_deployment(import_entrypoint, monkeypatch):
    """Having these in a shell or .env while working on the proxy is ordinary.
    Only a managed runtime means "deployed", so nothing fires off-cloud."""
    monkeypatch.setenv("ACCESS_SIGNING_SECRET", "local-dev-secret")
    monkeypatch.setenv("ADMIN_TOKEN", "local-dev-password")

    assert import_entrypoint().app is not None


def test_guard_can_be_overridden_deliberately(import_entrypoint, monkeypatch):
    """An escape hatch, matching ALLOW_LOCALHOST_APP_URL in proxy/access.py:
    the guard infers intent, so there has to be a way to say otherwise."""
    monkeypatch.setenv("K_SERVICE", "tracerlensai-app")
    monkeypatch.setenv("ACCESS_SIGNING_SECRET", "proxy-only-secret")
    monkeypatch.setenv("ALLOW_AGENT_ON_PROXY_SERVICE", "1")

    assert import_entrypoint().app is not None
