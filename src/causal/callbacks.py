"""Deterministic glue between the LLM agents and the causal engine.

All writes go through ``callback_context.state[...]`` so ADK records them as
``state_delta`` on the callback's event — one write is both persistence and
UI transport. No LLM calls happen here.
"""

from __future__ import annotations

import logging
from typing import Optional

from google.genai import types

from src.causal import state_keys as sk
from src.causal.complexity import is_effect_query
from src.causal.graph_engine import CausalTaskGraph
from src.causal.ledger import append_replan_event
from src.causal.models import (
    CausalDecomposition,
    CausalStatus,
    ExecutionPlan,
    ReplanRequest,
    ReplanResult,
    parse_model,
)
from src.causal.runtime import (
    next_ready_step,
    summarize_decomposition_line,
    summarize_pathway_line,
    summarize_replan_line,
)

logger = logging.getLogger(__name__)

_SKIP = types.Content(role="model", parts=[types.Part(text="")])


def _write_graph(state, graph: CausalTaskGraph) -> None:
    state[sk.KEY_GRAPH_FULL] = graph.to_state()
    state[sk.KEY_GRAPH] = graph.to_ui_graph()


def build_graph_and_plan(callback_context) -> Optional[types.Content]:
    """Decomposer after-callback: LLM decomposition -> validated DAG -> plan."""
    _ensure_graph_built(callback_context)
    return None


def _ensure_graph_built(callback_context) -> None:
    state = callback_context.state
    if state.get(sk.KEY_GRAPH_FULL):
        return

    raw = state.get(sk.KEY_DECOMPOSITION_RAW)
    dec = parse_model(CausalDecomposition, raw)
    if dec is None or not dec.components:
        state[sk.KEY_STATUS] = CausalStatus(
            phase="failed",
            note="decomposition output was empty or unparseable",
        ).model_dump(mode="json")
        logger.warning("Causal decomposition unparseable; pipeline degrades to plain answer.")
        return

    graph = CausalTaskGraph.from_decomposition(dec)
    budgets = state.get(sk.KEY_BUDGETS) or {}
    max_steps = int(budgets.get("max_steps", sk.DEFAULT_MAX_STEPS))
    plan = graph.derive_plan(max_steps)

    # Append, never replace. The router has already written its [budget] line
    # and the web ingestor its [web] line, and the proxy streams trace lines by
    # diffing against a high-water mark — so overwriting the list here made the
    # new entries land at an index it had already sent, and the [graph]/[plan]
    # lines never reached the live timeline.
    steps_trace = list(state.get(sk.KEY_STEPS) or [])
    steps_trace.append(
        summarize_decomposition_line(len(graph.model.components), len(graph.model.edges)))
    steps_trace.append(summarize_pathway_line(plan.rationale))
    for note in graph.model.repair_notes:
        steps_trace.append(f"[graph] repair: {note}")

    first = next_ready_step(plan)
    if first is not None:
        first.status = "running"
        graph.set_component_status(first.component_id, "active")
        state[sk.KEY_CURRENT_STEP] = first.model_dump(mode="json")
    else:
        state[sk.KEY_CURRENT_STEP] = None

    _write_graph(state, graph)
    state[sk.KEY_PLAN] = plan.model_dump(mode="json")
    state[sk.KEY_STEPS] = steps_trace
    state[sk.KEY_STATUS] = CausalStatus(
        phase="executing" if first is not None else "synthesizing",
        plan_version=plan.version,
    ).model_dump(mode="json")


def skip_if_aborted(callback_context) -> Optional[types.Content]:
    """LoopAgent before-callback: skip the whole loop when decomposition failed."""
    status = parse_model(CausalStatus, callback_context.state.get(sk.KEY_STATUS))
    if status is not None and status.phase in ("failed", "synthesizing", "complete"):
        return _SKIP
    return None


def skip_if_no_ready_step(callback_context) -> Optional[types.Content]:
    """StepExecutor before-callback: never spend an LLM call without a step.

    Also rebuilds the graph defensively if the decomposer's output_key write
    landed after its after-callback ran (ADK version variance).
    """
    _ensure_graph_built(callback_context)
    if not callback_context.state.get(sk.KEY_CURRENT_STEP):
        return _SKIP
    return None


def skip_unless_effect_query(callback_context) -> Optional[types.Content]:
    """EstimandSpec before-callback: the estimand stage costs 0 LLM calls unless
    the query actually asks for a treatment effect DoWhy can identify. Also skips
    when decomposition failed, so the graph-less degrade path stays cheap.

    Effect detection is the lexical regex OR-ed with the decomposer's semantic
    is_effect_query flag (the decomposer runs first), so paraphrases the regex
    misses still get the formal path."""
    state = callback_context.state
    status = parse_model(CausalStatus, state.get(sk.KEY_STATUS))
    if status is not None and status.phase == "failed":
        return _SKIP
    if is_effect_query(state.get(sk.KEY_QUERY) or ""):
        return None
    dec = parse_model(CausalDecomposition, state.get(sk.KEY_DECOMPOSITION_RAW))
    if dec is not None and dec.is_effect_query:
        return None
    return _SKIP


def skip_unless_web_requested(callback_context) -> Optional[types.Content]:
    """CausalWebSearch before-callback: the web-retrieval LLM call is spent only
    when the user turned the 'Add observation data from the web' toggle on
    (router set KEY_WEB_REQUESTED). Otherwise 0 LLM calls."""
    state = callback_context.state
    status = parse_model(CausalStatus, state.get(sk.KEY_STATUS))
    if status is not None and status.phase == "failed":
        return _SKIP
    if state.get(sk.KEY_WEB_REQUESTED):
        return None
    return _SKIP


def skip_unless_replan_requested(callback_context) -> Optional[types.Content]:
    """Replanner before-callback: replanning costs 0 LLM calls on the happy path."""
    state = callback_context.state
    status = parse_model(CausalStatus, state.get(sk.KEY_STATUS))
    if status is not None and status.phase in ("failed", "complete", "budget_exhausted", "synthesizing"):
        return _SKIP
    if not state.get(sk.KEY_REPLAN_REQUEST):
        return _SKIP
    return None


def splice_replan(callback_context) -> Optional[types.Content]:
    """Replanner after-callback: splice replacement steps into the plan."""
    state = callback_context.state
    request = parse_model(ReplanRequest, state.get(sk.KEY_REPLAN_REQUEST))
    if request is None:
        return None

    replan = parse_model(ReplanResult, state.get(sk.KEY_REPLAN_RAW))
    graph_raw = state.get(sk.KEY_GRAPH_FULL)
    plan = parse_model(ExecutionPlan, state.get(sk.KEY_PLAN))
    status = parse_model(CausalStatus, state.get(sk.KEY_STATUS)) or CausalStatus()

    if replan is None or not replan.new_steps or graph_raw is None or plan is None:
        status.phase = "failed"
        status.note = "replanner produced no usable steps"
        state[sk.KEY_STATUS] = status.model_dump(mode="json")
        state[sk.KEY_REPLAN_REQUEST] = None
        logger.warning("Replan unusable; causal loop will terminate.")
        return None

    graph = CausalTaskGraph.from_state(graph_raw)
    plan, event = graph.splice(plan, replan, request)

    steps_trace = list(state.get(sk.KEY_STEPS) or [])
    steps_trace.append(summarize_replan_line(event))
    # Keep the structured event too, not just its prose summary: which steps
    # were invalidated and which replaced them is the record of *why* the plan
    # changed, and a flattened line cannot be queried or rendered.
    append_replan_event(state, event)

    nxt = next_ready_step(plan)
    if nxt is not None:
        nxt.status = "running"
        graph.set_component_status(nxt.component_id, "active")
        state[sk.KEY_CURRENT_STEP] = nxt.model_dump(mode="json")
        status.phase = "executing"
    else:
        state[sk.KEY_CURRENT_STEP] = None
        status.phase = "synthesizing"

    status.plan_version = plan.version
    _write_graph(state, graph)
    state[sk.KEY_PLAN] = plan.model_dump(mode="json")
    state[sk.KEY_STEPS] = steps_trace
    state[sk.KEY_STATUS] = status.model_dump(mode="json")
    state[sk.KEY_REPLAN_REQUEST] = None
    state[sk.KEY_REPLAN_RAW] = None
    return None


def mark_complete(callback_context) -> None:
    state = callback_context.state
    status_raw = state.get(sk.KEY_STATUS)
    if status_raw:
        status = parse_model(CausalStatus, status_raw)
        if status:
            status.phase = "complete"
            state[sk.KEY_STATUS] = status.model_dump(mode="json")
