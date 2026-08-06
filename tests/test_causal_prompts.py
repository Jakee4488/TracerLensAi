"""Instruction providers render without blowing up, and cite the way the UI expects.

Ported from a root-level `test_prompt.py` scratch script that pytest collected
by accident (it matched `test_*.py` at rootdir and executed at import). The
script also appended "src" to sys.path and imported `causal.*`, which created a
second, distinct copy of every model class — enough to break the isinstance
check in models.parse_model. These import `src.causal.*` like the rest of the
suite.
"""
from src.causal import state_keys as sk
from src.causal.models import (
    CausalGraph,
    CausalStatus,
    Component,
    ExecutionPlan,
    PlanStep,
)
from src.causal.prompts import synthesizer_instruction


class _Ctx:
    """Stands in for an ADK callback context — the providers only read .state."""

    def __init__(self, state):
        self.state = state


def _state(**overrides):
    graph = CausalGraph(components=[
        Component(id="c1", label="Test Node", kind="process", description=""),
    ])
    plan = ExecutionPlan(steps=[
        PlanStep(id="s1", component_id="c1", objective="Do test",
                 result_summary="Success", status="done"),
    ])
    state = {
        sk.KEY_GRAPH_FULL: graph.model_dump(mode="json"),
        sk.KEY_PLAN: plan.model_dump(mode="json"),
        sk.KEY_STATUS: CausalStatus().model_dump(mode="json"),
    }
    state.update(overrides)
    return state


def test_synthesizer_instruction_renders_a_completed_plan():
    prompt = synthesizer_instruction(_Ctx(_state()))
    assert prompt
    assert "Do test" in prompt
    assert "Success" in prompt


def test_synthesizer_instruction_cites_nodes_by_label():
    """The `[Node: <label>]` form is the contract with the UI's citation
    linkifier — it is what turns a mention into a click that highlights the
    DAG. Renaming the marker here silently breaks that."""
    prompt = synthesizer_instruction(_Ctx(_state()))
    assert "[Node: Test Node]" in prompt


def test_synthesizer_instruction_survives_an_empty_state():
    """The provider runs on every causal turn, including ones where the
    pipeline failed before writing a plan. It must not raise there."""
    assert synthesizer_instruction(_Ctx({})) is not None
