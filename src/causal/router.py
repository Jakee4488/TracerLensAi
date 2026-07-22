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
from src.causal.complexity import budgets_for_query, tier_for_query
from src.causal.models import CausalStatus


def is_causal_request(text: str) -> bool:
    return sk.CAUSAL_MODE_MARKER in (text or "")


def strip_marker(text: str) -> str:
    return (text or "").replace(sk.CAUSAL_MODE_MARKER, "").strip()


def _budgets_from_env() -> dict:
    """Env-configured budget ceilings. When CAUSAL_MAX_STEPS/REPLANS are unset
    these fall back to the module defaults; when set they cap the dynamic
    per-query budget (see _budgets_for_query)."""
    def _read(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, "") or default))
        except ValueError:
            return default

    return {
        "max_steps": _read("CAUSAL_MAX_STEPS", sk.DEFAULT_MAX_STEPS),
        "max_replans": _read("CAUSAL_MAX_REPLANS", sk.DEFAULT_MAX_REPLANS),
    }


def _budgets_for_query(query: str) -> tuple[dict, str]:
    """Per-query budget sized by complexity, clamped by the env ceilings.

    Returns (budgets, tier) so the router can also emit a UI trace line.
    """
    ceiling = _budgets_from_env()
    dynamic = budgets_for_query(query)
    budgets = {
        "max_steps": min(dynamic["max_steps"], ceiling["max_steps"]),
        "max_replans": min(dynamic["max_replans"], ceiling["max_replans"]),
    }
    return budgets, tier_for_query(query)


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
        # turn in this session, then seed complexity-sized budgets and phase.
        query = strip_marker(text)
        budgets, tier = _budgets_for_query(query)
        reset: dict = {key: None for key in sk.ALL_KEYS}
        reset[sk.KEY_BUDGETS] = budgets
        # Persist the original problem text so the step executor -- which runs
        # with include_contents="none" and therefore never sees the user
        # message -- can access the raw data it needs to compute on.
        reset[sk.KEY_QUERY] = query
        reset[sk.KEY_STEPS] = [
            f"[budget] complexity: {tier.replace('_', ' ')} -> "
            f"{budgets['max_steps']} steps / {budgets['max_replans']} replans"
        ]
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
