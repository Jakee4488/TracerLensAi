"""Unit tests for the deterministic query-complexity scorer and the router's
env-clamped dynamic budgets."""

import pytest

from src.causal.complexity import (
    TIER_BUDGETS,
    budgets_for_query,
    score_query_complexity,
    tier_for_query,
)
from src.causal.router import _budgets_for_query


# ── Scorer ────────────────────────────────────────────────────────────────────

def test_empty_query_is_simple():
    assert score_query_complexity("") == 0
    assert score_query_complexity("   ") == 0
    assert tier_for_query("") == "simple"
    assert budgets_for_query("") == {"max_steps": 3, "max_replans": 1}


def test_trivial_query_is_simple():
    assert tier_for_query("hi") == "simple"
    assert tier_for_query("say hello") == "simple"
    assert budgets_for_query("hi") == {"max_steps": 3, "max_replans": 1}


def test_short_causal_question_climbs_off_the_floor():
    # A causal keyword alone lifts a short query above trivial.
    assert score_query_complexity("why did sales drop?") >= 2
    assert tier_for_query("why did sales drop?") in ("moderate", "complex")


def test_long_multiclause_causal_comparison_is_very_complex():
    query = (
        "Analyse the causal drivers of Q3 customer churn in our SaaS product, "
        "compare the impact of support latency versus pricing changes, and "
        "explain why onboarding friction leads to cancellations if the trial "
        "period is too short, then trace the downstream effect on revenue."
    )
    assert score_query_complexity(query) >= 7
    assert tier_for_query(query) == "very_complex"
    assert budgets_for_query(query) == {"max_steps": 12, "max_replans": 2}


def test_attachment_contents_do_not_inflate_complexity():
    # A big comma/word-heavy pasted file must not push a trivial question up
    # a tier — complexity is about the question, not the data size.
    csv_dump = "month,revenue,churn\n" + "\n".join(
        f"2024-{m:02d},{1000 + m},{0.01 * m}" for m in range(1, 40)
    )
    with_attachment = (
        f"--- Attached file: data.csv ---\n{csv_dump}\n--- End of file: data.csv ---\n\n"
        "summarise this"
    )
    assert tier_for_query(with_attachment) == tier_for_query("summarise this")
    assert budgets_for_query(with_attachment) == {"max_steps": 3, "max_replans": 1}


def test_scores_are_monotonic_across_tiers():
    simple = score_query_complexity("hello")
    moderate = score_query_complexity("why did the metric change over the last month?")
    complex_q = score_query_complexity(
        "Compare why revenue fell and how the pricing change and the outage "
        "each impacted churn, and what the downstream effect was."
    )
    assert simple < moderate <= complex_q


def test_every_tier_maps_to_a_budget():
    for tier, budget in TIER_BUDGETS.items():
        assert budget["max_steps"] >= 1
        assert budget["max_replans"] >= 0
    # speed-biased ordering: steps grow with tier
    steps = [TIER_BUDGETS[t]["max_steps"] for t in ("simple", "moderate", "complex", "very_complex")]
    assert steps == sorted(steps)


# ── Router env clamp ──────────────────────────────────────────────────────────

def test_router_uses_dynamic_budget_when_no_env(monkeypatch):
    monkeypatch.delenv("CAUSAL_MAX_STEPS", raising=False)
    monkeypatch.delenv("CAUSAL_MAX_REPLANS", raising=False)
    budgets, tier = _budgets_for_query("hi")
    assert tier == "simple"
    assert budgets == {"max_steps": 3, "max_replans": 1}


def test_env_clamps_dynamic_budget_down(monkeypatch):
    # A very-complex query normally gets 12 steps; the env ceiling caps it.
    monkeypatch.setenv("CAUSAL_MAX_STEPS", "4")
    monkeypatch.setenv("CAUSAL_MAX_REPLANS", "1")
    query = (
        "Analyse the causal drivers of churn, compare support latency versus "
        "pricing, and explain why onboarding friction leads to cancellations, "
        "then trace the downstream effect on revenue and margins over time."
    )
    budgets, tier = _budgets_for_query(query)
    assert tier == "very_complex"
    assert budgets["max_steps"] == 4     # clamped from 12
    assert budgets["max_replans"] == 1   # clamped from 2


def test_env_does_not_inflate_below_dynamic(monkeypatch):
    # A high env ceiling never raises a simple query above its dynamic budget.
    monkeypatch.setenv("CAUSAL_MAX_STEPS", "16")
    monkeypatch.setenv("CAUSAL_MAX_REPLANS", "5")
    budgets, _ = _budgets_for_query("hello there")
    assert budgets == {"max_steps": 3, "max_replans": 1}
