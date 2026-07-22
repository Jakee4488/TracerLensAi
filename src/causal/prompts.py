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
    ExecutionPlan,
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
        "- Ignore control markers such as [[causal:on]] in the message.\n"
        "Return ONLY JSON matching the response schema."
    )


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
        + f"Analysis results (for your grounding -- rewrite, do not quote verbatim):\n{results or '(none)'}\n"
        "Internal notes (context only -- never quote or reference these):\n"
        + "\n".join(f"- {line}" for line in steps_trace[-15:])
    )
