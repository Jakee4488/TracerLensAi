from typing import Any, Dict, List, Optional

from src.observability.prompt_analysis import estimate_tokens


COMPOUNDING_FORMULA = "E_total ≈ N · P + N(N−1)/2 · (O_avg + T_avg)"


def optimize_workflow(
    original_prompt: str,
    trace: List[Dict[str, Any]],
    expected_loops: Optional[int] = None,
    prompt_analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Analyse an agentic workflow trace and project token costs.

    Uses the compounding math formula to estimate how context
    accumulates across sequential agent loops, then returns
    projection data, dry-run transitions, optimisation tips,
    and (optionally) piped prompt-analysis results.

    Args:
        original_prompt: The raw user prompt.
        trace: Execution trace steps from the orchestrator.
        expected_loops: Override for the number of agent loops.
            When ``None`` the loop count is inferred from the trace.
        prompt_analysis: Pre-computed prompt analysis dict.  When
            provided its tips and optimised prompt are merged into
            the response.
    """
    prompt = original_prompt.strip()
    inferred_loops = _infer_loop_count(trace)
    loop_count = max(1, expected_loops or inferred_loops)
    base_prompt_tokens = _base_prompt_tokens(prompt, trace)
    average_model_output_tokens = _average_tokens(
        trace, ("llm_call", "response", "re_prompt")
    )
    average_tool_output_tokens = _average_tokens(trace, ("tool_call",))

    compounding_history_tokens = round(
        (loop_count * (loop_count - 1) / 2) *
        (average_model_output_tokens + average_tool_output_tokens)
    )
    estimated_input_tokens = round(
        (loop_count * base_prompt_tokens) + compounding_history_tokens
    )
    estimated_output_tokens = round(loop_count * average_model_output_tokens)
    estimated_total_tokens = estimated_input_tokens + estimated_output_tokens

    dry_run_transitions = _build_dry_run_transitions(trace, base_prompt_tokens)
    risk_level = _risk_level(
        loop_count, estimated_total_tokens, average_tool_output_tokens
    )

    compounding_chart = _build_compounding_chart(
        loop_count=loop_count,
        base_prompt_tokens=base_prompt_tokens,
        average_model_output_tokens=average_model_output_tokens,
        average_tool_output_tokens=average_tool_output_tokens,
    )

    cost_per_step = _build_cost_per_step(dry_run_transitions)

    # Build workflow-level tips
    workflow_tips = _build_optimization_tips(
        loop_count=loop_count,
        base_prompt_tokens=base_prompt_tokens,
        average_tool_output_tokens=average_tool_output_tokens,
        dry_run_transitions=dry_run_transitions,
    )

    # Merge prompt-analysis tips when present
    merged_tips = _merge_tips(workflow_tips, prompt_analysis)

    result: Dict[str, Any] = {
        "original_prompt": prompt,
        "compounding_formula": COMPOUNDING_FORMULA,
        "assumptions": {
            "expected_loops": loop_count,
            "base_prompt_tokens": base_prompt_tokens,
            "average_model_output_tokens": average_model_output_tokens,
            "average_tool_output_tokens": average_tool_output_tokens,
        },
        "projection": {
            "estimated_input_tokens": estimated_input_tokens,
            "estimated_output_tokens": estimated_output_tokens,
            "estimated_total_tokens": estimated_total_tokens,
            "compounding_history_tokens": compounding_history_tokens,
            "cost_multiplier": round(
                estimated_total_tokens / max(base_prompt_tokens, 1), 2
            ),
            "risk_level": risk_level,
        },
        "compounding_chart": compounding_chart,
        "cost_per_step": cost_per_step,
        "dry_run_transitions": dry_run_transitions,
        "optimization_tips": merged_tips,
        "optimized_workflow": _build_optimized_workflow(
            loop_count, average_tool_output_tokens
        ),
        "golden_dataset_plan": _build_golden_dataset_plan(),
    }

    # Attach prompt analysis when available
    if prompt_analysis is not None:
        result["prompt_analysis"] = {
            "efficiency_score": prompt_analysis.get("efficiency_score"),
            "optimized_prompt": prompt_analysis.get("optimized_prompt"),
            "prompt_tips": prompt_analysis.get("optimization_tips", []),
        }

    return result


# ── Helpers ──────────────────────────────────────────────────────────────────


def _infer_loop_count(trace: List[Dict[str, Any]]) -> int:
    loop_steps = {"llm_call", "tool_call", "re_prompt", "policy_check", "escalation"}
    return max(1, sum(1 for step in trace if step.get("step_type") in loop_steps))


def _base_prompt_tokens(prompt: str, trace: List[Dict[str, Any]]) -> int:
    user_input_step = next(
        (step for step in trace if step.get("step_type") == "user_input"), None
    )
    if user_input_step and user_input_step.get("tokens"):
        return max(int(user_input_step["tokens"]), estimate_tokens(prompt))
    return estimate_tokens(prompt)


def _average_tokens(
    trace: List[Dict[str, Any]], step_types: tuple[str, ...]
) -> int:
    tokens = [
        int(step.get("tokens", 0))
        for step in trace
        if step.get("step_type") in step_types and int(step.get("tokens", 0)) > 0
    ]
    if not tokens:
        return 0
    return round(sum(tokens) / len(tokens))


def _build_dry_run_transitions(
    trace: List[Dict[str, Any]], base_prompt_tokens: int
) -> List[Dict[str, Any]]:
    cumulative_context_tokens = base_prompt_tokens
    transitions: List[Dict[str, Any]] = []

    for index, step in enumerate(trace, start=1):
        emitted_tokens = int(step.get("tokens", 0))
        transition_input_tokens = cumulative_context_tokens
        transitions.append(
            {
                "step": index,
                "step_type": step.get("step_type", "unknown"),
                "description": step.get("description", "Workflow step"),
                "transition_input_tokens": transition_input_tokens,
                "emitted_tokens": emitted_tokens,
                "cumulative_context_tokens": transition_input_tokens + emitted_tokens,
            }
        )
        if step.get("step_type") != "user_input":
            cumulative_context_tokens += emitted_tokens

    return transitions


def _build_compounding_chart(
    loop_count: int,
    base_prompt_tokens: int,
    average_model_output_tokens: int,
    average_tool_output_tokens: int,
) -> List[Dict[str, Any]]:
    """Return per-step data points for the compounding token chart.

    Each entry contains:
    - step: loop iteration (1-indexed)
    - input_tokens: tokens submitted to the model at this step
    - output_tokens: tokens the model emits at this step
    - cumulative: running total of all tokens consumed so far
    """
    chart: List[Dict[str, Any]] = []
    cumulative = 0
    history_tokens = 0

    for step in range(1, loop_count + 1):
        input_tokens = base_prompt_tokens + history_tokens
        output_tokens = average_model_output_tokens
        step_total = input_tokens + output_tokens
        cumulative += step_total
        chart.append(
            {
                "step": step,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "history_tokens": history_tokens,
                "cumulative": cumulative,
            }
        )
        # Next iteration adds this step's output + tool output to history
        history_tokens += average_model_output_tokens + average_tool_output_tokens

    return chart


def _build_cost_per_step(
    dry_run_transitions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Summarise cost accumulation at each trace step."""
    return [
        {
            "step": t["step"],
            "step_type": t["step_type"],
            "description": t["description"],
            "context_window": t["cumulative_context_tokens"],
        }
        for t in dry_run_transitions
    ]


def _build_optimization_tips(
    loop_count: int,
    base_prompt_tokens: int,
    average_tool_output_tokens: int,
    dry_run_transitions: List[Dict[str, Any]],
) -> List[str]:
    tips: List[str] = []
    if loop_count > 3:
        tips.append(
            "Set an explicit recursion or loop limit so failed tool paths "
            "cannot compound context indefinitely."
        )
    if average_tool_output_tokens > max(300, base_prompt_tokens * 4):
        tips.append(
            "Summarize or slice tool payloads before adding them to agent memory."
        )
    if dry_run_transitions:
        largest_step = max(dry_run_transitions, key=lambda s: s["emitted_tokens"])
        if largest_step["emitted_tokens"] > max(120, base_prompt_tokens * 3):
            tips.append(
                f"Reduce the payload emitted by {largest_step['description']} "
                "before the next model call."
            )
    tips.append(
        "Run deterministic graph dry-runs with mocked LLM and tool responses "
        "before production traffic."
    )
    tips.append(
        "Maintain a golden dataset of 10 to 30 representative prompts to "
        "baseline average token budgets."
    )
    return tips


def _merge_tips(
    workflow_tips: List[str],
    prompt_analysis: Optional[Dict[str, Any]],
) -> List[str]:
    """Merge workflow-level tips with prompt-analysis tips, de-duplicated."""
    merged = list(workflow_tips)
    if prompt_analysis:
        prompt_tips = prompt_analysis.get("optimization_tips", [])
        existing_lower = {t.lower() for t in merged}
        for tip in prompt_tips:
            if tip.lower() not in existing_lower:
                merged.append(tip)
    return merged


def _build_optimized_workflow(
    loop_count: int, average_tool_output_tokens: int
) -> List[str]:
    workflow = [
        "Keep system instructions and the user request as the stable base prompt.",
        "At each graph transition, count the full state object before the model call.",
        "Store large tool outputs outside chat history and pass back a compact "
        "summary or identifier.",
    ]
    if loop_count > 3:
        workflow.append(
            "Stop after the configured loop limit and return a recovery response "
            "instead of re-prompting."
        )
    if average_tool_output_tokens > 0:
        workflow.append(
            "Mock tool responses in dry-runs, then compare projected and observed "
            "token totals."
        )
    workflow.append(
        "Trace golden dataset runs in MLflow, LangSmith, Langfuse, or an "
        "equivalent observability sink."
    )
    return workflow


def _build_golden_dataset_plan() -> List[str]:
    return [
        "Select 10 to 30 representative user inputs across easy, average, "
        "and failure-prone paths.",
        "Run the dataset through the real workflow in an isolated environment.",
        "Record token use per node, per edge, and per full run.",
        "Set rollout budgets from p50, p90, and worst-case traces before "
        "scaling traffic.",
    ]


def _risk_level(
    loop_count: int,
    estimated_total_tokens: int,
    average_tool_output_tokens: int,
) -> str:
    if (
        loop_count >= 6 or
        estimated_total_tokens >= 12000 or
        average_tool_output_tokens >= 1500
    ):
        return "high"
    if (
        loop_count >= 3 or
        estimated_total_tokens >= 4000 or
        average_tool_output_tokens >= 500
    ):
        return "medium"
    return "low"
