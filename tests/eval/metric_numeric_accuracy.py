"""Deterministic arithmetic metric for `agents-cli eval grade`.

Wired in from eval_config.yaml as `custom_function_file: metric_numeric_accuracy.py`.
One `evaluate` per file is what that contract expects, so this is a thin
entry point over `assertions.py`, where the logic and its tests live.

What it does: for every numeric ground truth declared in expectations.json for
this case, extract the value the agent actually produced and assert it is
within tolerance. Values come from the final answer prose and, preferably,
from typed floats in the node traces — so an INTERMEDIATE result (the
estimator's `effect.point`) can be checked, not just the last number written.

No LLM, no network: a stated ATE either lands within tolerance of the known
SCM ground truth or it does not. Score is the fraction of checks that passed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assertions  # noqa: E402


def evaluate(instance):
    return assertions.evaluate_case(instance, "numeric_accuracy")
