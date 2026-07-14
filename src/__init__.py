"""TracerLensAi package exports.

Lazy (PEP 562) so that importing pure submodules like ``src.causal`` does not
pull in the serving stack (AdkApp / FastAPI app), which requires GCP
credentials at import time.
"""

__all__ = ["adk_app", "engine", "fastapi_app", "root_agent"]


def __getattr__(name):
    if name in ("adk_app", "engine", "root_agent"):
        from src import agent as _agent
        return {"adk_app": _agent.adk_app, "engine": _agent.app,
                "root_agent": _agent.root_agent}[name]
    if name == "fastapi_app":
        from src.fast_api_app import app as fastapi_app
        return fastapi_app
    raise AttributeError(f"module 'src' has no attribute {name!r}")
