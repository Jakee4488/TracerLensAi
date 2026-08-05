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
def fresh_fast_api_app(monkeypatch):
    """Import src.fast_api_app under stubbed GCP credentials, guaranteed fresh.

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

    for name in list(sys.modules):
        if name == "src.fast_api_app" or name.startswith("src.fast_api_app."):
            del sys.modules[name]

    import src.fast_api_app as fast_api_app
    return fast_api_app


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
