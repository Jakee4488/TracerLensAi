"""Instruction providers for the causal pipeline agents.

Callables ``(ReadonlyContext) -> str`` instead of ``{state_key}`` template
strings: no ADK templating KeyErrors on missing keys, and the prompt size
stays bounded — the executor/replanner see O(step) context, never the whole
conversation or graph.
"""

from __future__ import annotations

from src.causal import state_keys as sk
from src.causal.models import (
    CausalStatus,
    CounterfactualResult,
    EffectEstimate,
    ExecutionPlan,
    IdentificationResult,
    PlanStep,
    ReplanRequest,
    parse_model,
)


def decomposer_instruction(ctx) -> str:
    return (
        "You are a causal analyst. Decompose the user's problem into its important "
        "components and the directed causal relations between them.\n"
        f"- At most {sk.MAX_COMPONENTS} components; ids in snake_case; short labels.\n"
        "- Classify each component kind: input, process, artifact, constraint, or outcome. "
        "Include exactly one 'outcome' component representing the goal.\n"
        "- Edges are directed source -> target meaning 'source causally affects target', "
        "with relation (causes, enables, constrains, informs) and confidence 0-1 "
        "plus a one-line rationale.\n"
        "- 'goal' is a one-sentence restatement of what the user wants.\n"
        "- Set is_effect_query=true ONLY when the user asks for the quantitative "
        "effect of one variable on another (effect/impact of X on Y, treatment "
        "effect, elasticity, 'how much would X change Y'); false otherwise.\n"
        "- Ignore control markers such as [[causal:on]] in the message.\n"
        "Return ONLY JSON matching the response schema."
    )


def estimand_spec_instruction(ctx) -> str:
    """Emit a VARIABLE-level DAG (distinct from the decomposer's task graph) so
    DoWhy can identify the treatment->outcome estimand deterministically."""
    from src.causal.estimation import dataset_headers

    query = ctx.state.get(sk.KEY_QUERY) or ""
    headers = dataset_headers(query)
    header_line = (
        f"- Dataset columns detected (use these EXACT ids for any variable that "
        f"matches a column, so estimation can find them): {', '.join(headers)}.\n"
    ) if headers else ""
    return (
        "You are a causal-inference analyst. The user asks for the effect of one variable "
        "(the TREATMENT) on another (the OUTCOME). Extract a VARIABLE-LEVEL causal DAG so a "
        "formal identification algorithm (back-door / instrumental-variable) can run.\n"
        "- List the real-world variables: the treatment, the outcome, and every common "
        "cause (confounder), instrument, and mediator you can reasonably infer.\n"
        "- Give each a snake_case id, a short label, and a role: treatment, outcome, "
        "confounder, instrument, mediator, or other. Exactly one treatment and one outcome.\n"
        "- 'edges' are directed source -> target meaning 'source directly causes target'. "
        "Include confounder->treatment AND confounder->outcome edges so back-door paths are "
        "visible; do NOT invent edges you have no reason to believe.\n"
        "- 'treatment' and 'outcome' must be ids that appear in 'variables'.\n"
        + header_line +
        "- If the question is counterfactual and names concrete treatment values "
        "('had price stayed at 10 instead of 12'), set baseline_value and "
        "intervention_value to those numbers; otherwise leave them null.\n"
        "- Ignore control markers such as [[causal:on]].\n"
        f"User request:\n{query}\n"
        "Return ONLY JSON matching the response schema."
    )


def _estimand_grounding(state) -> str:
    """Formal-identification block injected into the executor and synthesizer so
    the LLM's numeric estimate is anchored to DoWhy's adjustment set instead of
    ad-hoc confounder guessing. Empty when the estimand stage did not run."""
    ident = parse_model(IdentificationResult, state.get(sk.KEY_ESTIMAND))
    if ident is None:
        return ""
    if not ident.identifiable:
        return (
            f"Formal identification (DoWhy): the effect of '{ident.treatment}' on "
            f"'{ident.outcome}' is NOT identifiable from the stated causal graph "
            f"({ident.note or 'no valid adjustment set'}). Be explicit about this "
            "limitation rather than reporting a falsely precise number."
        )

    adj = ", ".join(ident.adjustment_set) if ident.adjustment_set else \
        "no variables (no back-door confounding)"
    lines = [
        "Formal identification (DoWhy) — use this, do not re-derive it:",
        f"- Estimate the effect of '{ident.treatment}' on '{ident.outcome}' via {ident.estimand_type}.",
        f"- Adjust for exactly: {adj}. Do NOT condition on mediators or colliders outside "
        "this set — doing so biases the estimate.",
    ]
    if ident.estimand_type == "iv" and ident.instruments:
        lines.append(f"- Instrument(s): {', '.join(ident.instruments)}.")
    if ident.estimand_expr:
        lines.append(f"- Estimand: {ident.estimand_expr}")

    effect = parse_model(EffectEstimate, state.get(sk.KEY_EFFECT))
    if effect is not None and effect.method:
        ci = ""
        if effect.ci_low is not None and effect.ci_high is not None:
            ci = f" (95% CI {effect.ci_low:.4g} to {effect.ci_high:.4g})"
        lines.append(
            f"- A dataset was provided: estimated effect = {effect.point:.4g}{ci} via "
            f"{effect.method} on {effect.n_obs} rows. Treat this computed number as authoritative."
        )
        if effect.refutations:
            checks = "; ".join(f"{r.method}: {'passed' if r.passed else 'FAILED'}"
                               for r in effect.refutations)
            lines.append(f"- Robustness checks — {checks}.")
    else:
        lines.append(
            "- No dataset was provided, so estimate the magnitude yourself, grounded "
            "strictly on the adjustment set above."
        )

    cf = parse_model(CounterfactualResult, state.get(sk.KEY_COUNTERFACTUAL))
    if cf is not None:
        lines.append(
            f"- Counterfactual (SCM fitted on the data): under do({cf.treatment}="
            f"{cf.intervention_value:.4g}) the average {cf.outcome} is "
            f"{cf.intervention_outcome:.4g}, vs {cf.baseline_outcome:.4g} under "
            f"do({cf.treatment}={cf.baseline_value:.4g}) — difference {cf.delta:+.4g}. "
            "Treat these computed values as authoritative."
        )
    return "\n".join(lines)


def step_executor_instruction(ctx) -> str:
    state = ctx.state
    step = parse_model(PlanStep, state.get(sk.KEY_CURRENT_STEP))
    graph = state.get(sk.KEY_GRAPH_FULL) or {}
    goal = graph.get("goal", "") if isinstance(graph, dict) else ""
    ledger = state.get(sk.KEY_LEDGER) or []
    recent = "\n".join(
        f"- {e.get('step_id')}: {e.get('verdict')} — {e.get('observed', '')[:120]}"
        for e in ledger[-3:]
    )

    if step is None:
        # Skip-guard should prevent this; degrade to a no-op with a failure trailer.
        return (
            "No executable step is available. Reply with exactly:\n"
            "OBSERVED: no step to execute\nSTEP_STATUS: failure"
        )

    query = state.get(sk.KEY_QUERY)

    lines = [
        f"You are executing ONE step of a causal plan. Overall goal: {goal or '(see conversation)'}",
        f"Step {step.id} targets component '{step.component_id}'.",
        f"Objective: {step.objective}",
    ]
    if query:
        # This agent runs with include_contents="none", so the original problem
        # statement and any numeric data are NOT in the conversation history --
        # supply them here so computational steps have their inputs.
        lines.append(f"Given problem and data (from the user):\n{query}")
    if step.expected_effect:
        lines.append(f"Expected effect: {step.expected_effect}")
    grounding = _estimand_grounding(state)
    if grounding:
        lines.append(grounding)
    if recent:
        lines.append(f"Recent change ledger:\n{recent}")
    lines.append(
        "Execute ONLY this step. Use Python code when computation, modeling, or "
        "verification helps. Be concise. End your reply with exactly two lines:\n"
        "OBSERVED: <one line - what actually resulted or changed>\n"
        "STEP_STATUS: success|failure"
    )
    return "\n".join(lines)


def replanner_instruction(ctx) -> str:
    request = parse_model(ReplanRequest, ctx.state.get(sk.KEY_REPLAN_REQUEST))
    if request is None:
        return "No replan is requested. Return JSON with an empty new_steps list and reason 'nothing to do'."

    comps = "\n".join(
        f"- {c.id} ({c.kind}, status={c.status}): {c.label}"
        for c in request.subgraph_components
    )
    edges = "\n".join(
        f"- {e.source} -> {e.target} ({e.relation}, {e.confidence:.2f})"
        for e in request.subgraph_edges
    )
    return (
        "A plan step failed and its causal impact invalidated the steps listed below. "
        "Produce replacement steps ONLY for the affected components — completed work on "
        "unaffected components must not be redone.\n"
        f"Failed step {request.failed_step.id} on component '{request.failed_step.component_id}': "
        f"{request.failed_step.objective}\n"
        f"Observed: {request.observed}\n"
        f"Invalidated steps: {', '.join(request.invalidated_step_ids) or 'none'}\n"
        f"Affected components:\n{comps or '- (none)'}\n"
        f"Causal links within the affected subgraph:\n{edges or '- (none)'}\n"
        f"Return at most {request.max_new_steps} new steps, each with component_id "
        "(one of the affected components), objective, expected_effect, and depends_on "
        "(existing completed step ids only). Fix the root cause first. "
        "Return ONLY JSON matching the response schema."
    )


def synthesizer_instruction(ctx) -> str:
    state = ctx.state
    graph = state.get(sk.KEY_GRAPH_FULL) or {}
    goal = graph.get("goal", "") if isinstance(graph, dict) else ""
    status = parse_model(CausalStatus, state.get(sk.KEY_STATUS)) or CausalStatus()
    plan = parse_model(ExecutionPlan, state.get(sk.KEY_PLAN))
    steps_trace = state.get(sk.KEY_STEPS) or []

    # Feed the model the substance of each step (what it was for and what it
    # found) WITHOUT step ids or status flags -- those are execution-engine
    # internals that must never reach the user-facing answer.
    results = ""
    if plan:
        results = "\n".join(
            f"- {s.objective[:120]}" + (f" => {s.result_summary}" if s.result_summary else "")
            for s in plan.steps
            if s.result_summary or s.status != "pending"
        )

    outcome_note = {
        "synthesizing": "",
        "complete": "",
        "budget_exhausted": (
            "Some analysis could not be fully completed. Present the strongest "
            "answer supported by the results below. If a specific quantity or "
            "conclusion genuinely could not be determined, say so in one plain "
            "sentence -- do not enumerate internal steps or 'what remains undone'."
        ),
        "failed": (
            "The structured analysis did not run. Answer the user's question "
            "directly and well from your own reasoning, without referring to any "
            "pipeline or plan."
        ),
    }.get(status.phase, "")

    grounding = _estimand_grounding(state)

    return (
        "You are writing the FINAL user-facing answer. Address the goal below "
        "directly, grounded in the analysis results.\n"
        "Lead with the answer and the key quantitative results, then a brief, "
        "plain-language explanation of the causal reasoning (confounders, "
        "adjustment, direction/size of effect).\n"
        "STRICT: Write as a self-contained answer to the user. Never mention the "
        "plan, steps, step ids (e.g. s1, s6.r1), replanning, budgets, status "
        "phases, control markers, or internal state. The reader has no idea an "
        "internal pipeline exists. No 'Replanning' or 'What Remains Undone' "
        "sections.\n"
        f"Goal: {goal or '(the user message in this conversation)'}\n"
        + (f"{outcome_note}\n" if outcome_note else "")
        + (f"{grounding}\n" if grounding else "")
        + f"Analysis results (for your grounding -- rewrite, do not quote verbatim):\n{results or '(none)'}\n"
        "Internal notes (context only -- never quote or reference these):\n"
        + "\n".join(f"- {line}" for line in steps_trace[-15:])
    )
