"""Deterministic root router: causal pipeline vs. the general assistant.

An LLM router would need the auto-injected transfer function declarations,
which Vertex refuses to mix with built-in code execution (see the note in
src/agent.py about Search + Code Execution) — and would cost one LLM call per
turn. Marker matching costs zero.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions

from src.causal import state_keys as sk
from src.causal.models import CausalStatus


def is_causal_request(text: str) -> bool:
    return sk.CAUSAL_MODE_MARKER in (text or "")


def strip_marker(text: str) -> str:
    return (text or "").replace(sk.CAUSAL_MODE_MARKER, "").strip()


def _budgets_from_env() -> dict:
    def _read(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, "") or default))
        except ValueError:
            return default

    return {
        "max_steps": _read("CAUSAL_MAX_STEPS", sk.DEFAULT_MAX_STEPS),
        "max_replans": _read("CAUSAL_MAX_REPLANS", sk.DEFAULT_MAX_REPLANS),
    }


class CausalRouterAgent(BaseAgent):
    """Routes each message by control marker; resets causal state per causal turn."""

    def __init__(self, name: str, general_assistant: BaseAgent,
                 causal_pipeline: BaseAgent, **kwargs):
        super().__init__(
            name=name,
            description=(
                "TracerLensAi root agent: causal-reasoning pipeline for marked "
                "requests, general assistant otherwise."
            ),
            sub_agents=[general_assistant, causal_pipeline],
            **kwargs,
        )

    @property
    def _general(self) -> BaseAgent:
        return self.sub_agents[0]

    @property
    def _pipeline(self) -> BaseAgent:
        return self.sub_agents[1]

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        text = self._user_text(ctx)
        if not is_causal_request(text):
            async for event in self._general.run_async(ctx):
                yield event
            return

        # Fresh causal turn: clear any stale causal_* state from a previous
        # turn in this session, then seed budgets and phase.
        reset: dict = {key: None for key in sk.ALL_KEYS}
        reset[sk.KEY_BUDGETS] = _budgets_from_env()
        reset[sk.KEY_STEPS] = []
        reset[sk.KEY_LEDGER] = []
        reset[sk.KEY_STATUS] = CausalStatus(phase="decomposing").model_dump(mode="json")
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            branch=getattr(ctx, "branch", None),
            actions=EventActions(state_delta=reset),
        )
        async for event in self._pipeline.run_async(ctx):
            yield event

    @staticmethod
    def _user_text(ctx) -> str:
        content = getattr(ctx, "user_content", None)
        parts = getattr(content, "parts", None) or []
        return " ".join(p.text for p in parts if getattr(p, "text", None))
