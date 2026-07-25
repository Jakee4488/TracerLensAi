"""CausalEstimator: the deterministic statistical-inference stage (zero LLM).

Runs immediately after the estimand-spec LLM. Reads the variable-level DAG that
stage produced, runs DoWhy identification (always) and estimation + refutation
(only when a dataset was attached), and writes the results to session state as
one ``state_delta`` — persistence and UI transport in a single write, exactly
like CausalStepController.

When no estimand spec is present — the stage was skipped for a non-effect query,
or the spec was unparseable — this is a silent no-op, so the common path costs
nothing.
"""

from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions

from src.causal import state_keys as sk
from src.causal.complexity import is_counterfactual_query
from src.causal.estimation import parse_dataset, run_counterfactual, run_identification
from src.causal.models import CausalEstimand, parse_model
from src.causal.runtime import (
    summarize_counterfactual_line,
    summarize_effect_line,
    summarize_estimand_line,
)


class CausalEstimator(BaseAgent):
    """Deterministic DoWhy identification/estimation node (no LLM)."""

    def __init__(self, name: str = "CausalEstimator", **kwargs):
        super().__init__(
            name=name,
            description="Deterministic DoWhy identification + estimation (no LLM).",
            **kwargs,
        )

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        spec = parse_model(CausalEstimand, state.get(sk.KEY_ESTIMAND_SPEC_RAW))
        if spec is None:
            return  # stage skipped (non-effect query) or spec unparseable

        try:
            df = parse_dataset(state.get(sk.KEY_QUERY) or "")
        except Exception:
            df = None

        ident, effect = run_identification(spec, df)

        # Rung 3: only for counterfactual-phrased queries with a dataset (an
        # SCM must be fitted); run_counterfactual returns None on any failure.
        counterfactual = None
        if df is not None and is_counterfactual_query(state.get(sk.KEY_QUERY) or ""):
            counterfactual = run_counterfactual(spec, df)

        trace = list(state.get(sk.KEY_STEPS) or [])
        trace.append(summarize_estimand_line(ident))
        if effect is not None:
            trace.append(summarize_effect_line(effect))
        if counterfactual is not None:
            trace.append(summarize_counterfactual_line(counterfactual))

        delta = {
            sk.KEY_ESTIMAND: ident.model_dump(mode="json"),
            sk.KEY_EFFECT: effect.model_dump(mode="json") if effect is not None else None,
            sk.KEY_COUNTERFACTUAL: (counterfactual.model_dump(mode="json")
                                    if counterfactual is not None else None),
            sk.KEY_STEPS: trace,
        }
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            branch=getattr(ctx, "branch", None),
            actions=EventActions(state_delta=delta),
        )
