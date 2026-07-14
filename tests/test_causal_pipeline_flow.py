"""Integration test of the causal pipeline control flow — no LLM, no ADK
runtime. Drives the deterministic pieces (decomposer callback, controller,
replanner splice callback) against fake contexts exactly as the LoopAgent
sequences them, covering: plan derivation, success advance, failure with
impact propagation, localized replan splicing, completion escalation, budget
exhaustion, and graceful degradation on unparseable decomposition.
"""

import asyncio
import json

import pytest

from src.causal import state_keys as sk
from src.causal.callbacks import build_graph_and_plan, splice_replan
from src.causal.controller import CausalStepController
from src.causal.models import CausalStatus


class FakeCallbackContext:
    def __init__(self, state):
        self.state = state


class FakeSession:
    def __init__(self, state):
        self.state = state
        self.events = []


class FakeCtx:
    def __init__(self, state):
        self.session = FakeSession(state)
        self.invocation_id = "inv-1"
        self.branch = None


def run_controller(state):
    """One controller iteration; applies its state_delta like the runner would."""
    async def _run():
        controller = CausalStepController()
        events = [e async for e in controller._run_async_impl(FakeCtx(state))]
        assert len(events) == 1, "controller must yield exactly one event"
        state.update(events[0].actions.state_delta or {})
        return events[0]
    return asyncio.get_event_loop().run_until_complete(_run())


DECOMPOSITION = json.dumps({
    "goal": "Forecast revenue after 10% price increase",
    "components": [
        {"id": "data", "label": "Sales data", "kind": "input"},
        {"id": "elasticity", "label": "Estimate elasticity", "kind": "process"},
        {"id": "scenarios", "label": "Scenario computation", "kind": "process"},
        {"id": "forecast", "label": "Revenue forecast", "kind": "outcome"},
    ],
    "edges": [
        {"source": "data", "target": "elasticity", "confidence": 0.9},
        {"source": "elasticity", "target": "scenarios", "confidence": 0.85},
        {"source": "scenarios", "target": "forecast", "confidence": 0.9},
    ],
})


def seeded_state(max_replans=2):
    state = {
        sk.KEY_BUDGETS: {"max_steps": 8, "max_replans": max_replans},
        sk.KEY_STATUS: CausalStatus(phase="decomposing").model_dump(mode="json"),
        sk.KEY_DECOMPOSITION_RAW: DECOMPOSITION,
    }
    build_graph_and_plan(FakeCallbackContext(state))
    return state


def test_full_loop_with_failure_propagation_and_replan():
    state = seeded_state()

    # Decomposition -> graph + plan + first step running.
    assert state[sk.KEY_STATUS]["phase"] == "executing"
    assert [s["component_id"] for s in state[sk.KEY_PLAN]["steps"]] == [
        "elasticity", "scenarios", "forecast"]
    assert state[sk.KEY_CURRENT_STEP]["id"] == "s1"

    # Iteration 1: s1 succeeds, plan advances.
    state[sk.KEY_STEP_OUTPUT] = "OBSERVED: elasticity -1.4\nSTEP_STATUS: success"
    event = run_controller(state)
    assert not event.actions.escalate
    assert state[sk.KEY_CURRENT_STEP]["id"] == "s2"

    # Iteration 2: s2 fails -> impact propagates to forecast, replan requested.
    state[sk.KEY_STEP_OUTPUT] = "OBSERVED: scenario matrix is singular\nSTEP_STATUS: failure"
    event = run_controller(state)
    assert not event.actions.escalate
    assert state[sk.KEY_STATUS]["phase"] == "replanning"
    request = state[sk.KEY_REPLAN_REQUEST]
    assert request["failed_step"]["id"] == "s2"
    assert request["affected_component_ids"] == ["forecast"]
    assert request["invalidated_step_ids"] == ["s3"]
    nodes = {n["id"]: n["status"] for n in state[sk.KEY_GRAPH]["nodes"]}
    assert nodes["scenarios"] == "failed"
    assert nodes["forecast"] == "invalidated"
    assert nodes["elasticity"] == "done"  # completed unaffected work preserved

    # Replanner output -> splice: replacement steps enter the plan.
    state[sk.KEY_REPLAN_RAW] = json.dumps({
        "reason": "use regularized scenario grid",
        "new_steps": [
            {"component_id": "scenarios", "objective": "Recompute with regularization",
             "depends_on": ["s1"]},
            {"component_id": "forecast", "objective": "Recompute forecast"},
        ],
    })
    splice_replan(FakeCallbackContext(state))
    assert state[sk.KEY_STATUS]["phase"] == "executing"
    assert state[sk.KEY_CURRENT_STEP]["id"] == "s2.r1"
    assert state[sk.KEY_PLAN]["version"] == 2

    # Iterations 3+4: replacement steps succeed -> completion escalation.
    state[sk.KEY_STEP_OUTPUT] = "OBSERVED: scenarios computed\nSTEP_STATUS: success"
    run_controller(state)
    assert state[sk.KEY_CURRENT_STEP]["id"] == "s2.r2"
    state[sk.KEY_STEP_OUTPUT] = "OBSERVED: forecast -3.2% revenue\nSTEP_STATUS: success"
    event = run_controller(state)
    assert event.actions.escalate
    status = state[sk.KEY_STATUS]
    assert status["phase"] == "synthesizing"
    assert status["replans_used"] == 1 and status["executed_steps"] == 4

    verdicts = [(r["step_id"], r["verdict"]) for r in state[sk.KEY_LEDGER]]
    assert verdicts == [("s1", "success"), ("s2", "failure"),
                        ("s2.r1", "success"), ("s2.r2", "success")]
    assert any(line.startswith("[replan] v1->v2") for line in state[sk.KEY_STEPS])


def test_failure_without_replan_budget_escalates():
    state = seeded_state(max_replans=0)
    state[sk.KEY_STEP_OUTPUT] = "OBSERVED: boom\nSTEP_STATUS: failure"
    event = run_controller(state)
    assert event.actions.escalate
    assert state[sk.KEY_STATUS]["phase"] == "budget_exhausted"
    assert state.get(sk.KEY_REPLAN_REQUEST) is None


def test_unparseable_decomposition_degrades_gracefully():
    state = {sk.KEY_DECOMPOSITION_RAW: "not json at all"}
    build_graph_and_plan(FakeCallbackContext(state))
    assert state[sk.KEY_STATUS]["phase"] == "failed"
    assert sk.KEY_PLAN not in state


def test_unusable_replan_fails_closed():
    state = seeded_state()
    state[sk.KEY_STEP_OUTPUT] = "OBSERVED: boom\nSTEP_STATUS: failure"
    run_controller(state)
    assert state[sk.KEY_STATUS]["phase"] == "replanning"
    state[sk.KEY_REPLAN_RAW] = json.dumps({"reason": "??", "new_steps": []})
    splice_replan(FakeCallbackContext(state))
    assert state[sk.KEY_STATUS]["phase"] == "failed"
    # Next controller pass terminates the loop.
    event = run_controller(state)
    assert event.actions.escalate


def test_deviation_from_missing_trailer_triggers_replan():
    state = seeded_state()
    state[sk.KEY_STEP_OUTPUT] = "rambling output with no trailer"
    run_controller(state)
    assert state[sk.KEY_STATUS]["phase"] == "replanning"
    assert state[sk.KEY_LEDGER][-1]["verdict"] == "deviation"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
