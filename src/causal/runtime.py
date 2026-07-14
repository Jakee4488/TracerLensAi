"""Pure decision helpers used by the step controller.

Everything is deterministic and ADK-free so it unit-tests hermetically:
verdict parsing from the executor's output trailer, ready-step selection,
completion/budget checks, and the human-readable trace lines shown in the
UI's causal panel.
"""

from __future__ import annotations

import re
from typing import Optional

from src.causal.models import CausalStatus, ExecutionPlan, PlanStep, ReplanEvent, Verdict
from src.causal.state_keys import OBSERVED_RE, STEP_STATUS_RE

_STATUS_RE = re.compile(STEP_STATUS_RE, re.IGNORECASE)
_OBSERVED_RE = re.compile(OBSERVED_RE, re.IGNORECASE)


def parse_step_verdict(step_output: str, code_outcomes: Optional[list[str]] = None) -> tuple[Verdict, str]:
    """Parse the executor's ``OBSERVED:`` / ``STEP_STATUS:`` trailer.

    A missing trailer is a 'deviation' (propagates like a failure but is
    flagged as such); a failed code-execution outcome downgrades a claimed
    success to a deviation.
    """
    text = step_output or ""
    status_matches = _STATUS_RE.findall(text)
    observed_matches = _OBSERVED_RE.findall(text)
    observed = observed_matches[-1].strip()[:300] if observed_matches else ""

    code_failed = any("OUTCOME_FAILED" in (o or "") for o in (code_outcomes or []))

    if not status_matches:
        return "deviation", observed or "no STEP_STATUS trailer in step output"
    verdict = status_matches[-1].lower()
    if verdict == "success" and code_failed:
        return "deviation", observed or "code execution failed despite claimed success"
    return ("success", observed) if verdict == "success" else ("failure", observed)


def next_ready_step(plan: ExecutionPlan) -> Optional[PlanStep]:
    """First pending step whose dependencies are all done/skipped, in plan
    order (plan order is topological by construction)."""
    resolved = {s.id for s in plan.steps if s.status in ("done", "skipped")}
    for step in plan.steps:
        if step.status == "pending" and all(dep in resolved for dep in step.depends_on):
            return step
    return None


def plan_complete(plan: ExecutionPlan) -> bool:
    return all(s.status not in ("pending", "running") for s in plan.steps)


def budgets_exhausted(status: CausalStatus, budgets: dict) -> Optional[str]:
    """A reason string when a hard budget is spent, else None."""
    max_steps = int(budgets.get("max_steps", 0) or 0)
    if max_steps and status.executed_steps >= max_steps:
        return f"step budget exhausted ({status.executed_steps}/{max_steps})"
    return None


def replan_budget_left(status: CausalStatus, budgets: dict) -> bool:
    max_replans = int(budgets.get("max_replans", 0) or 0)
    return status.replans_used < max_replans


# ── UI trace lines ────────────────────────────────────────────────────────────

_VERDICT_MARK = {"success": "[ok]", "failure": "[FAIL]", "deviation": "[deviation]"}


def summarize_step_line(step: PlanStep, verdict: Verdict, observed: str,
                        affected: Optional[list[str]] = None) -> str:
    line = f"{_VERDICT_MARK[verdict]} {step.id} ({step.component_id}): {step.objective[:80]}"
    if observed:
        line += f" | observed: {observed[:120]}"
    if affected:
        line += f" | impact -> {', '.join(affected)}"
    return line


def summarize_replan_line(event: ReplanEvent) -> str:
    new = ", ".join(event.new_step_ids) or "none"
    invalidated = ", ".join(event.invalidated_step_ids) or "none"
    return (
        f"[replan] v{event.plan_version_from}->v{event.plan_version_to} "
        f"from {event.failed_step_id}: invalidated {invalidated}; new steps {new}"
        + (f" | {event.reason[:120]}" if event.reason else "")
    )


def summarize_decomposition_line(n_components: int, n_edges: int) -> str:
    return f"[graph] decomposed problem into {n_components} components, {n_edges} causal links"


def summarize_pathway_line(rationale: str) -> str:
    return f"[plan] {rationale}"
