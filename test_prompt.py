import json
from pydantic import BaseModel
import sys

sys.path.append("src")
from causal.models import CausalGraph, Component, ExecutionPlan, PlanStep, CausalStatus
from causal.prompts import synthesizer_instruction
import causal.state_keys as sk

class MockCtx:
    def __init__(self, state):
        self.state = state

graph = CausalGraph(
    components=[Component(id="c1", label="Test Node", kind="process", description="")]
)
plan = ExecutionPlan(
    steps=[PlanStep(id="s1", component_id="c1", objective="Do test", result_summary="Success", status="done")]
)

state = {
    sk.KEY_GRAPH_FULL: graph.model_dump(mode="json"),
    sk.KEY_PLAN: plan.model_dump(mode="json"),
    sk.KEY_STATUS: CausalStatus().model_dump(mode="json"),
}

try:
    prompt = synthesizer_instruction(MockCtx(state))
    print("PROMPT START==================")
    print(prompt)
    print("PROMPT END====================")
except Exception as e:
    import traceback
    traceback.print_exc()
