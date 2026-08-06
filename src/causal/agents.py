"""Factories wiring the causal pipeline into ADK agents.

Isolation invariant (guarded by tests/test_causal_agents.py): every LlmAgent
carries at most ONE of {code_executor, output_schema, tools} and none carries
sub_agents — Vertex refuses built-in tools mixed with function declarations,
so all deterministic work lives in callbacks and custom agents instead of
FunctionTools.
"""

from __future__ import annotations

import json
import os
from typing import AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, SequentialAgent
from google.adk.code_executors import BuiltInCodeExecutor
from google.adk.events import Event, EventActions
from google.adk.tools import google_search
from google.genai import types

from src.causal import prompts
from src.causal import state_keys as sk
from src.causal.callbacks import (
    build_graph_and_plan,
    mark_complete,
    skip_if_aborted,
    skip_if_no_ready_step,
    skip_unless_effect_query,
    skip_unless_replan_requested,
    skip_unless_web_requested,
    splice_replan,
)
from src.causal.controller import CausalStepController
from src.causal.estimation import parse_web_retrieval
from src.causal.estimator import CausalEstimator
from src.causal.models import CausalDecomposition, CausalEstimand, ReplanResult
from src.causal.router import CausalRouterAgent
from src.causal.runtime import summarize_web_line

MODEL = "gemini-2.5-flash"

_DETERMINISTIC = types.GenerateContentConfig(temperature=0)


class CausalWebIngestor(BaseAgent):
    """Deterministic parser (0 LLM) for the CausalWebSearch output: extracts a
    fetched CSV or evidence bullets into state so downstream stages can use them.
    No-op unless the web toggle was on. Never raises."""

    def __init__(self, name: str = "CausalWebIngestor", **kwargs):
        super().__init__(name=name, description="Parses web-search output into data/evidence.", **kwargs)

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if not state.get(sk.KEY_WEB_REQUESTED):
            return
        web, csv_text = parse_web_retrieval(state.get(sk.KEY_WEB_SEARCH_RAW) or "")
        trace = list(state.get(sk.KEY_STEPS) or [])
        trace.append(summarize_web_line(web))
        delta = {
            sk.KEY_WEB_DATASET: csv_text,
            # evidence and sources ride inside KEY_WEB_RETRIEVAL, which is what
            # the prompts and the UI actually read; writing them out separately
            # only duplicated the payload on every web-mode turn.
            sk.KEY_WEB_RETRIEVAL: web.model_dump(mode="json"),
            sk.KEY_STEPS: trace,
        }
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            branch=getattr(ctx, "branch", None),
            actions=EventActions(state_delta=delta),
        )


class CausalFallbackEmitter(BaseAgent):
    """Deterministic text transport used only when CAUSAL_TEXT_FALLBACK=1:
    emits the causal results as a fenced ```causal-json``` block for proxies
    that cannot read event state deltas. Zero LLM calls; silent by default."""

    def __init__(self, name: str = "CausalFallbackEmitter", **kwargs):
        super().__init__(name=name, description="Optional fenced-JSON transport fallback.", **kwargs)

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        if os.environ.get("CAUSAL_TEXT_FALLBACK") != "1":
            return
        state = ctx.session.state
        payload = {
            "steps": state.get(sk.KEY_STEPS) or [],
            "graph": state.get(sk.KEY_GRAPH),
            "status": state.get(sk.KEY_STATUS),
            "estimand": state.get(sk.KEY_ESTIMAND),
            "effect": state.get(sk.KEY_EFFECT),
            "counterfactual": state.get(sk.KEY_COUNTERFACTUAL),
            "graph_reconcile": state.get(sk.KEY_GRAPH_RECONCILE),
            "web_retrieval": state.get(sk.KEY_WEB_RETRIEVAL),
            "final_answer": state.get(sk.KEY_FINAL) or "",
        }
        block = f"```{sk.FENCED_BLOCK_LANG}\n{json.dumps(payload)}\n```"
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            branch=getattr(ctx, "branch", None),
            content=types.Content(role="model", parts=[types.Part(text=block)]),
        )


def build_causal_pipeline() -> SequentialAgent:
    """[web search] -> decomposer -> bounded executor loop -> synthesizer (-> fallback)."""
    # Search-only LlmAgent: carries ONLY tools=[google_search] (no code_executor
    # / output_schema), keeping the Vertex tool-isolation invariant. Skip-gated:
    # 0 LLM calls unless the 'Add observation data from the web' toggle is on.
    web_search = LlmAgent(
        name="CausalWebSearch",
        model=MODEL,
        description="Fetches best-effort observational data / evidence from the web.",
        instruction=prompts.web_search_instruction,
        tools=[google_search],
        include_contents="none",
        output_key=sk.KEY_WEB_SEARCH_RAW,
        before_agent_callback=skip_unless_web_requested,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    decomposer = LlmAgent(
        name="CausalDecomposer",
        model=MODEL,
        description="Extracts components and causal relations as structured JSON.",
        instruction=prompts.decomposer_instruction,
        output_schema=CausalDecomposition,
        output_key=sk.KEY_DECOMPOSITION_RAW,
        after_agent_callback=build_graph_and_plan,
        generate_content_config=_DETERMINISTIC,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    # Structured variable-level DAG for DoWhy. Skip-gated: costs 0 LLM calls
    # unless the query actually asks for a treatment effect. output_schema only
    # (no code_executor/tools) keeps the Vertex tool-isolation invariant.
    estimand_spec = LlmAgent(
        name="CausalEstimandSpec",
        model=MODEL,
        description="Extracts a variable-level causal DAG + treatment/outcome as structured JSON.",
        instruction=prompts.estimand_spec_instruction,
        output_schema=CausalEstimand,
        output_key=sk.KEY_ESTIMAND_SPEC_RAW,
        include_contents="none",
        before_agent_callback=skip_unless_effect_query,
        generate_content_config=_DETERMINISTIC,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    executor = LlmAgent(
        name="CausalStepExecutor",
        model=MODEL,
        description="Executes exactly one plan step, with Python code execution.",
        instruction=prompts.step_executor_instruction,
        code_executor=BuiltInCodeExecutor(),
        include_contents="none",
        output_key=sk.KEY_STEP_OUTPUT,
        before_agent_callback=skip_if_no_ready_step,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    replanner = LlmAgent(
        name="CausalReplanner",
        model=MODEL,
        description="Replans only the failure-affected subgraph as structured JSON.",
        instruction=prompts.replanner_instruction,
        output_schema=ReplanResult,
        output_key=sk.KEY_REPLAN_RAW,
        include_contents="none",
        before_agent_callback=skip_unless_replan_requested,
        after_agent_callback=splice_replan,
        generate_content_config=_DETERMINISTIC,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    loop = LoopAgent(
        name="CausalExecutorLoop",
        description="Bounded execute/verify/replan loop over the causal plan.",
        max_iterations=sk.LOOP_MAX_ITERATIONS,
        sub_agents=[executor, CausalStepController(), replanner],
        before_agent_callback=skip_if_aborted,
    )

    synthesizer = LlmAgent(
        name="CausalSynthesizer",
        model=MODEL,
        description="Writes the final user-facing answer from the executed plan.",
        instruction=prompts.synthesizer_instruction,
        include_contents="none",
        output_key=sk.KEY_FINAL,
        after_agent_callback=mark_complete,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    return SequentialAgent(
        name="CausalPipeline",
        description="Causal reasoning pathway: decompose, identify, execute, replan, synthesize.",
        sub_agents=[
            web_search, CausalWebIngestor(), decomposer, estimand_spec,
            CausalEstimator(), loop, synthesizer, CausalFallbackEmitter(),
        ],
    )


def build_root_agent(general_assistant: BaseAgent,
                     name: str = "TracerLensAi_Agent") -> CausalRouterAgent:
    """Root router. Keeps the historical root name so the A2A agent card and
    traces stay stable."""
    return CausalRouterAgent(
        name=name,
        general_assistant=general_assistant,
        causal_pipeline=build_causal_pipeline(),
    )
