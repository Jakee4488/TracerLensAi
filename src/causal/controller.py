"""CausalStepController: the deterministic heart of the execution loop.

Runs after the step executor on every loop iteration. Parses the step verdict,
appends to the change ledger, propagates impact through the causal graph on
failure, requests a localized replan (or escalates when budgets are spent),
and advances the plan. Zero LLM calls. Each iteration yields exactly one event
whose state_delta is both the persistence write and the UI transport.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions

from src.causal import node_trace
from src.causal import state_keys as sk
from src.causal.graph_engine import CausalTaskGraph
from src.causal.ledger import append_to_state, next_seq
from src.causal.numeric import extract_numbers
from src.causal.models import (
    CausalStatus,
    ChangeRecord,
    ExecutionPlan,
    PlanStep,
    ReplanRequest,
    parse_model,
)
from src.causal.runtime import (
    budgets_exhausted,
    next_ready_step,
    parse_step_verdict,
    plan_complete,
    replan_budget_left,
    summarize_step_line,
)

_TERMINAL_PHASES = ("failed", "complete", "budget_exhausted", "synthesizing")


class CausalStepController(BaseAgent):
    """Deterministic verdict/propagation/replan-request controller."""

    def __init__(self, name: str = "CausalStepController", **kwargs):
        super().__init__(
            name=name,
            description="Deterministic causal plan controller (no LLM).",
            **kwargs,
        )

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        status = parse_model(CausalStatus, state.get(sk.KEY_STATUS)) or CausalStatus()
        delta: dict = {}

        if status.phase in _TERMINAL_PHASES:
            yield self._event(ctx, delta, escalate=True)
            return

        plan = parse_model(ExecutionPlan, state.get(sk.KEY_PLAN))
        graph_raw = state.get(sk.KEY_GRAPH_FULL)
        current = parse_model(PlanStep, state.get(sk.KEY_CURRENT_STEP))

        if plan is None or graph_raw is None:
            status.phase = "failed"
            status.note = "causal plan/graph missing from session state"
            delta[sk.KEY_STATUS] = status.model_dump(mode="json")
            yield self._event(ctx, delta, escalate=True)
            return
        graph = CausalTaskGraph.from_state(graph_raw)

        if current is None:
            # Nothing executed this iteration: either the plan is done or a
            # requested replan never produced a runnable step.
            if status.phase == "replanning":
                status.phase = "failed"
                status.note = "replan did not produce a runnable step"
            elif plan_complete(plan):
                status.phase = "synthesizing"
            else:
                status.phase = "failed"
                status.note = "no runnable steps remain (dependency deadlock)"
            delta[sk.KEY_STATUS] = status.model_dump(mode="json")
            yield self._event(ctx, delta, escalate=True)
            return

        # ── Verdict for the step the executor just ran ──────────────────────
        step_output = state.get(sk.KEY_STEP_OUTPUT) or ""
        verdict, observed = parse_step_verdict(step_output, self._code_outcomes(ctx))

        step = next((s for s in plan.steps if s.id == current.id), None)
        if step is None:  # defensive: state drifted
            step = current
            plan.steps.append(step)
        step.result_summary = observed
        step.attempt += 1
        status.executed_steps += 1

        steps_trace = list(state.get(sk.KEY_STEPS) or [])
        escalate = False

        if verdict == "success":
            step.status = "done"
            graph.set_component_status(step.component_id, "done")
            affected: list[str] = []
            steps_trace.append(summarize_step_line(step, verdict, observed))

            budget_note = budgets_exhausted(status, state.get(sk.KEY_BUDGETS) or {})
            if plan_complete(plan):
                status.phase = "synthesizing"
                escalate = True
                delta[sk.KEY_CURRENT_STEP] = None
            elif budget_note and next_ready_step(plan) is not None:
                status.phase = "budget_exhausted"
                status.note = budget_note
                escalate = True
                delta[sk.KEY_CURRENT_STEP] = None
            else:
                nxt = next_ready_step(plan)
                if nxt is None:
                    status.phase = "failed"
                    status.note = "no runnable steps remain (dependency deadlock)"
                    escalate = True
                    delta[sk.KEY_CURRENT_STEP] = None
                else:
                    nxt.status = "running"
                    graph.set_component_status(nxt.component_id, "active")
                    delta[sk.KEY_CURRENT_STEP] = nxt.model_dump(mode="json")
        else:
            # ── Failure/deviation: propagate impact, then replan or stop ────
            step.status = "failed"
            graph.set_component_status(step.component_id, "failed")
            affected_set = graph.propagate_impact(step.component_id)
            invalidated = graph.invalidate(plan, affected_set)
            affected = sorted(affected_set)
            steps_trace.append(summarize_step_line(step, verdict, observed, affected))

            if replan_budget_left(status, state.get(sk.KEY_BUDGETS) or {}):
                status.replans_used += 1
                status.phase = "replanning"
                comps, edges = graph.subgraph_slice(affected_set | {step.component_id})
                request = ReplanRequest(
                    failed_step=step,
                    observed=observed,
                    affected_component_ids=affected,
                    invalidated_step_ids=invalidated,
                    subgraph_components=comps,
                    subgraph_edges=edges,
                )
                delta[sk.KEY_REPLAN_REQUEST] = request.model_dump(mode="json")
                delta[sk.KEY_CURRENT_STEP] = None
            else:
                status.phase = "budget_exhausted"
                status.note = f"replan budget exhausted after {step.id} failed"
                steps_trace.append(f"[stop] {status.note}")
                escalate = True
                delta[sk.KEY_CURRENT_STEP] = None

        record = ChangeRecord(
            seq=next_seq(state.get(sk.KEY_LEDGER)),
            step_id=step.id,
            component_id=step.component_id,
            expected=step.expected_effect,
            observed=observed,
            verdict=verdict,
            affected=affected,
            plan_version=plan.version,
            ts=datetime.now(timezone.utc).isoformat(),
        )

        append_to_state(state, record, sink=delta)

        # Node trace for the component this step advanced: what it was asked to
        # do, what it reported back, and every number it stated. The numbers are
        # positional (observed_0, observed_1, ...) because the executor's
        # OBSERVED: trailer is free text with no declared schema — naming them
        # would mean guessing which quantity is which, and a wrong guess makes
        # an arithmetic check assert against the wrong thing.
        observed_numbers = extract_numbers(observed)
        node_trace.record(
            state,
            node_id=step.component_id,
            node_kind="step",
            stage=self.name,
            inputs={
                "step_id": step.id,
                "objective": step.objective,
                "expected_effect": step.expected_effect,
                "depends_on": list(step.depends_on),
                "attempt": step.attempt,
            },
            outputs={
                "verdict": verdict,
                "observed": observed,
                "affected": affected,
                "plan_version": plan.version,
            },
            values={f"observed_{i}": n.value for i, n in enumerate(observed_numbers)},
            note=f"{step.id} -> {verdict}",
            sink=delta,
        )

        delta[sk.KEY_PLAN] = plan.model_dump(mode="json")
        delta[sk.KEY_GRAPH_FULL] = graph.to_state()
        delta[sk.KEY_GRAPH] = graph.to_ui_graph()
        delta[sk.KEY_STEPS] = steps_trace
        delta[sk.KEY_STATUS] = status.model_dump(mode="json")
        delta[sk.KEY_STEP_OUTPUT] = None
        yield self._event(ctx, delta, escalate=escalate)

    def _event(self, ctx, delta: dict, escalate: bool = False) -> Event:
        return Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            branch=getattr(ctx, "branch", None),
            actions=EventActions(state_delta=delta, escalate=escalate),
        )

    def _code_outcomes(self, ctx) -> list[str]:
        """Code-execution outcomes since the last controller event — a
        secondary failure signal beside the STEP_STATUS trailer."""
        outcomes: list[str] = []
        for event in reversed(ctx.session.events or []):
            if event.author == self.name:
                break
            content = getattr(event, "content", None)
            for part in (getattr(content, "parts", None) or []):
                result = getattr(part, "code_execution_result", None)
                if result is not None:
                    outcomes.append(str(getattr(result, "outcome", "")))
        return outcomes
