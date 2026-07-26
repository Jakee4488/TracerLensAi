"""Pure decision helpers used by the step controller.

Everything is deterministic and ADK-free so it unit-tests hermetically:
verdict parsing from the executor's output trailer, ready-step selection,
completion/budget checks, and the human-readable trace lines shown in the
UI's causal panel.
"""

from __future__ import annotations

import re
from typing import Optional

from src.causal.models import (
    CausalStatus,
    CounterfactualResult,
    EffectEstimate,
    ExecutionPlan,
    GraphReconciliation,
    IdentificationResult,
    PlanStep,
    ReplanEvent,
    Verdict,
    WebRetrieval,
)
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


def summarize_estimand_line(ident: IdentificationResult) -> str:
    """One-line record of DoWhy's (data-free) identification result."""
    if not ident.identifiable:
        detail = ident.note or "no valid adjustment set for the stated graph"
        return f"[estimand] effect of {ident.treatment} on {ident.outcome} not identifiable — {detail}"[:200]
    adj = ", ".join(ident.adjustment_set) if ident.adjustment_set else "nothing (no back-door confounding)"
    line = (f"[estimand] effect of {ident.treatment} on {ident.outcome} "
            f"identifiable via {ident.estimand_type}; adjust for {adj}")
    if ident.estimand_type == "iv" and ident.instruments:
        line += f"; instruments {', '.join(ident.instruments)}"
    return line[:200]


def summarize_effect_line(effect: EffectEstimate) -> str:
    """One-line record of DoWhy's numeric estimate + robustness checks."""
    if not effect.method:
        return f"[effect] {effect.note or 'no numeric estimate produced'}"[:200]
    line = f"[effect] estimated effect ({effect.method}) = {effect.point:.4g}"
    if effect.ci_low is not None and effect.ci_high is not None:
        line += f" (95% CI {effect.ci_low:.4g} to {effect.ci_high:.4g})"
    if effect.refutations:
        checks = ", ".join(
            f"{r.method.split('_')[0]}:{'pass' if r.passed else 'FAIL'}"
            + (f"(p={r.p_value:.2f})" if r.p_value is not None else "")
            for r in effect.refutations
        )
        line += f"; robustness {checks}"
    return line[:200]


def _plural(n: int) -> str:
    return "" if n == 1 else "s"


def summarize_web_line(web: WebRetrieval) -> str:
    """One-line record of what the web-search branch fetched."""
    if web.mode == "dataset":
        return (f"[web] fetched {web.row_count}-row observational dataset "
                f"({web.n_sources} source{_plural(web.n_sources)})")[:200]
    if web.mode == "evidence":
        return (f"[web] no dataset found; grounded the DAG with {len(web.evidence)} "
                f"evidence snippet{_plural(len(web.evidence))} "
                f"({web.n_sources} source{_plural(web.n_sources)})")[:200]
    return "[web] search returned no usable data"


def summarize_reconcile_line(recon: GraphReconciliation) -> str:
    """One-line record of the data-driven DAG correction (causal discovery)."""
    if recon.verdict == "corrected":
        ex = "; ".join(f"{c.kind} {c.source}->{c.target}" for c in recon.changes[:2])
        line = f"[graph-fix] data corrected the DAG: {recon.n_changes} edit{_plural(recon.n_changes)}"
        if ex:
            line += f" — e.g. {ex}"
        if recon.latent_confounders:
            line += f"; suspected latent confounding: {', '.join(recon.latent_confounders[:2])}"
        return line[:200]
    if recon.verdict == "consistent":
        return "[graph-fix] data is consistent with the stated DAG (no edits)"
    return f"[graph-fix] DAG could not be tested against data ({recon.note or 'too dense / too few rows'})"[:200]


def summarize_counterfactual_line(cf: CounterfactualResult) -> str:
    """One-line record of the gcm counterfactual comparison (rung 3)."""
    return (
        f"[counterfactual] do({cf.treatment}={cf.intervention_value:.4g}) -> "
        f"{cf.outcome} {cf.intervention_outcome:.4g} vs "
        f"do({cf.treatment}={cf.baseline_value:.4g}) -> {cf.baseline_outcome:.4g}; "
        f"delta {cf.delta:+.4g}"
    )[:200]
