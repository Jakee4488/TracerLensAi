"""Unit tests for the pure runtime helpers (verdicts, scheduling, budgets)."""

from src.causal.models import CausalStatus, ExecutionPlan, PlanStep, ReplanEvent
from src.causal.runtime import (
    budgets_exhausted,
    next_ready_step,
    parse_step_verdict,
    plan_complete,
    replan_budget_left,
    summarize_replan_line,
    summarize_step_line,
)


def make_plan():
    return ExecutionPlan(steps=[
        PlanStep(id="s1", component_id="a", objective="do a"),
        PlanStep(id="s2", component_id="b", objective="do b", depends_on=["s1"]),
        PlanStep(id="s3", component_id="c", objective="do c", depends_on=["s2"]),
    ])


# ── Verdict parsing ───────────────────────────────────────────────────────────

def test_parse_verdict_success():
    verdict, observed = parse_step_verdict("work...\nOBSERVED: computed x=3\nSTEP_STATUS: success")
    assert verdict == "success" and observed == "computed x=3"


def test_parse_verdict_failure():
    verdict, observed = parse_step_verdict("OBSERVED: divide by zero\nSTEP_STATUS: failure")
    assert verdict == "failure" and observed == "divide by zero"


def test_parse_verdict_missing_trailer_is_deviation():
    verdict, observed = parse_step_verdict("some rambling text with no trailer")
    assert verdict == "deviation"
    assert "STEP_STATUS" in observed


def test_parse_verdict_last_trailer_wins_case_insensitive():
    text = "STEP_STATUS: failure\n...retry...\nObserved: fixed\nstep_status: SUCCESS"
    verdict, observed = parse_step_verdict(text)
    assert verdict == "success" and observed == "fixed"


def test_parse_verdict_code_failure_downgrades_success():
    verdict, _ = parse_step_verdict(
        "OBSERVED: fine\nSTEP_STATUS: success", code_outcomes=["OUTCOME_FAILED"])
    assert verdict == "deviation"
    verdict, _ = parse_step_verdict(
        "OBSERVED: fine\nSTEP_STATUS: success", code_outcomes=["Outcome.OUTCOME_OK"])
    assert verdict == "success"


# ── Scheduling ────────────────────────────────────────────────────────────────

def test_next_ready_step_respects_dependencies():
    plan = make_plan()
    assert next_ready_step(plan).id == "s1"
    plan.steps[0].status = "done"
    assert next_ready_step(plan).id == "s2"
    plan.steps[1].status = "invalidated"  # invalidated dep blocks s3
    assert next_ready_step(plan) is None


def test_next_ready_step_skipped_dep_counts_as_resolved():
    plan = make_plan()
    plan.steps[0].status = "skipped"
    assert next_ready_step(plan).id == "s2"


def test_plan_complete():
    plan = make_plan()
    assert not plan_complete(plan)
    for step in plan.steps:
        step.status = "done"
    assert plan_complete(plan)
    plan.steps[2].status = "invalidated"  # terminal state counts as settled
    assert plan_complete(plan)


# ── Budgets ───────────────────────────────────────────────────────────────────

def test_budgets_exhausted():
    status = CausalStatus(executed_steps=8)
    assert "step budget exhausted" in budgets_exhausted(status, {"max_steps": 8})
    assert budgets_exhausted(CausalStatus(executed_steps=3), {"max_steps": 8}) is None
    assert budgets_exhausted(status, {}) is None  # no budget configured


def test_replan_budget_left():
    assert replan_budget_left(CausalStatus(replans_used=0), {"max_replans": 2})
    assert not replan_budget_left(CausalStatus(replans_used=2), {"max_replans": 2})


# ── Trace lines ───────────────────────────────────────────────────────────────

def test_summarize_step_line():
    step = PlanStep(id="s1", component_id="model", objective="Fit the model")
    line = summarize_step_line(step, "failure", "singular matrix", ["forecast"])
    assert line.startswith("[FAIL] s1 (model)")
    assert "singular matrix" in line and "impact -> forecast" in line


def test_summarize_replan_line():
    event = ReplanEvent(seq=2, failed_step_id="s2", invalidated_step_ids=["s3"],
                        new_step_ids=["s2.r1"], plan_version_from=1,
                        plan_version_to=2, reason="regularize")
    line = summarize_replan_line(event)
    assert "v1->v2" in line and "s2.r1" in line and "regularize" in line
