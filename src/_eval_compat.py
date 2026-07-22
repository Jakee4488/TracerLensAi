"""Eval-SDK compatibility shim.

`agents-cli eval generate/grade` builds evaluation *metadata* for the agent
tree via ``vertexai._genai.types.evals.AgentConfig.from_agent``, whose
``instruction`` field is typed ``Optional[str]``. This project's causal agents
use ADK **instruction providers** — callables ``(ctx) -> str`` (see
``src/causal/prompts.py``) — which are a first-class, documented ADK feature but
make ``from_agent`` raise a pydantic ``ValidationError`` on the callable.

The CLI already patches the sibling method ``_get_tool_declarations_from_agent``
for the same class of SDK bug (see google/agents/cli/eval/_inference_runner.py
and https://github.com/googleapis/python-aiplatform/issues/6865); this shim
extends that fix to the ``instruction`` field.

Behavior-preserving: it only changes the static *string metadata* the eval
autorater reads about each agent. It does NOT touch runtime inference — ADK still
calls the real instruction provider when the agent actually runs.
"""

from __future__ import annotations


def patch_agent_config_instruction() -> None:
    """Coerce non-string ADK instruction providers to a readable label so the
    Vertex eval SDK's ``AgentConfig.from_agent`` doesn't crash. No-op if the SDK
    isn't installed or its layout changed."""
    try:
        from vertexai._genai.types.evals import AgentConfig
    except Exception:
        return

    if getattr(AgentConfig, "_tracerlens_instruction_patched", False):
        return

    _orig_from_agent = AgentConfig.from_agent.__func__  # unwrap classmethod

    def _from_agent(cls, agent):
        instruction = getattr(agent, "instruction", None)
        if instruction is not None and not isinstance(instruction, str):
            # Instruction provider (callable) or other non-str: represent it as a
            # stable label instead of invoking it (no ReadonlyContext here).
            label = getattr(instruction, "__name__", None) or type(instruction).__name__
            try:
                agent = agent.model_copy(update={"instruction": f"<dynamic: {label}>"})
            except Exception:
                # Fall back to a plain attribute swap if model_copy isn't available.
                try:
                    agent.instruction = f"<dynamic: {label}>"
                except Exception:
                    pass
        return _orig_from_agent(cls, agent)

    AgentConfig.from_agent = classmethod(_from_agent)
    AgentConfig._tracerlens_instruction_patched = True
