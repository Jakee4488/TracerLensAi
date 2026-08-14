"""Deterministic causal-node-path metric for `agents-cli eval grade`.

Wired in from eval_config.yaml as `custom_function_file: metric_node_path.py`.
One `evaluate` per file is what that contract expects, so this is a thin entry
point over `assertions.py`, where the logic and its tests live.

What it does: reads the node traces the agent emitted and asserts the reasoning
passed through the causal nodes it should have — which nodes were visited, in
what order, and what the identification node actually put in its adjustment
set. That last one is the point: "the analysis must not adjust for the
mediator" becomes a check against the formal estimand rather than against
whatever the prose claimed, which is not something a text judge can do
reliably.

This is the closest analogue to ADK tool-trajectory scoring that this agent's
architecture admits. It has no FunctionTools — Vertex tool isolation forbids
them — so there is no function_call sequence to score. The equivalent
observable is the sequence of causal nodes the pipeline traversed.

Requires the traces to be present: run `agents-cli eval generate` with
CAUSAL_NODE_TRACE=1. Without it these checks fail loudly rather than passing
silently, since a silent skip would look identical to a passing structural check.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assertions  # noqa: E402


def evaluate(instance):
    return assertions.evaluate_case(instance, "causal_node_path")
