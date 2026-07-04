from fastapi.testclient import TestClient

from src.main import app
from src.observability.workflow_optimizer import optimize_workflow


client = TestClient(app)

SAMPLE_TRACE = [
    {"step_type": "user_input", "description": "User Message Received", "tokens": 8, "latency_ms": 0.0},
    {"step_type": "llm_call", "description": "LLM Generation (vertex: gemini-2.5-pro)", "tokens": 120, "latency_ms": 850.0},
    {"step_type": "tool_call", "description": "Tool Execution: fetch_user_profile", "tokens": 30, "latency_ms": 510.0},
    {"step_type": "re_prompt", "description": "Re-prompt with Tool Results", "tokens": 60, "latency_ms": 0.0},
    {"step_type": "policy_check", "description": "Escalation Policy Check", "tokens": 0, "latency_ms": 0.5},
    {"step_type": "response", "description": "Response Delivered", "tokens": 45, "latency_ms": 0.0},
]


def test_optimize_workflow_returns_required_keys():
    """Verify all expected top-level keys are present."""
    result = optimize_workflow(
        original_prompt="Fetch user profile for user 123",
        trace=SAMPLE_TRACE,
    )

    expected_keys = {
        "original_prompt",
        "compounding_formula",
        "assumptions",
        "projection",
        "compounding_chart",
        "cost_per_step",
        "dry_run_transitions",
        "optimization_tips",
        "optimized_workflow",
        "golden_dataset_plan",
    }
    assert expected_keys.issubset(set(result.keys()))


def test_projection_values_are_positive():
    """Projection totals must be positive for a non-trivial trace."""
    result = optimize_workflow(
        original_prompt="Fetch user profile for user 123",
        trace=SAMPLE_TRACE,
    )
    proj = result["projection"]
    assert proj["estimated_total_tokens"] > 0
    assert proj["estimated_input_tokens"] > 0
    assert proj["cost_multiplier"] > 0
    assert proj["risk_level"] in ("low", "medium", "high")


def test_compounding_chart_length_matches_loop_count():
    """The chart array should have one entry per inferred loop."""
    result = optimize_workflow(
        original_prompt="Hello!",
        trace=SAMPLE_TRACE,
    )
    expected_loops = result["assumptions"]["expected_loops"]
    assert len(result["compounding_chart"]) == expected_loops


def test_compounding_chart_cumulative_increases():
    """Cumulative tokens should be strictly increasing."""
    result = optimize_workflow(
        original_prompt="Hello!",
        trace=SAMPLE_TRACE,
    )
    chart = result["compounding_chart"]
    for i in range(1, len(chart)):
        assert chart[i]["cumulative"] >= chart[i - 1]["cumulative"]


def test_prompt_analysis_piped_when_provided():
    """When prompt_analysis dict is supplied, the response includes it."""
    mock_pa = {
        "efficiency_score": 72,
        "optimized_prompt": "Fetch user profile for user 123.",
        "optimization_tips": ["Remove filler phrasing such as please."],
    }
    result = optimize_workflow(
        original_prompt="Please fetch user profile for user 123",
        trace=SAMPLE_TRACE,
        prompt_analysis=mock_pa,
    )
    assert "prompt_analysis" in result
    assert result["prompt_analysis"]["efficiency_score"] == 72
    assert result["prompt_analysis"]["optimized_prompt"] == "Fetch user profile for user 123."


def test_merged_tips_contain_prompt_analysis_tips():
    """When prompt analysis is piped, its tips should be merged."""
    unique_pa_tip = "Replace vague qualifiers with concrete success criteria or limits."
    mock_pa = {
        "efficiency_score": 60,
        "optimized_prompt": "Do X",
        "optimization_tips": [unique_pa_tip],
    }
    result = optimize_workflow(
        original_prompt="Do X",
        trace=SAMPLE_TRACE,
        prompt_analysis=mock_pa,
    )
    assert unique_pa_tip in result["optimization_tips"]


def test_prompt_analysis_absent_when_not_provided():
    """When prompt_analysis is None, the key should not appear."""
    result = optimize_workflow(
        original_prompt="Do X",
        trace=SAMPLE_TRACE,
        prompt_analysis=None,
    )
    assert "prompt_analysis" not in result


def test_expected_loops_override():
    """Caller can override the inferred loop count."""
    result = optimize_workflow(
        original_prompt="Do X",
        trace=SAMPLE_TRACE,
        expected_loops=10,
    )
    assert result["assumptions"]["expected_loops"] == 10
    assert len(result["compounding_chart"]) == 10


def test_golden_dataset_plan_non_empty():
    """Golden dataset plan should always have items."""
    result = optimize_workflow(
        original_prompt="Do X",
        trace=SAMPLE_TRACE,
    )
    assert len(result["golden_dataset_plan"]) >= 1


# ── Endpoint tests ───────────────────────────────────────────────────────


def test_optimize_workflow_endpoint_returns_200():
    """The /optimize-workflow endpoint should return 200 for valid input."""
    response = client.post(
        "/optimize-workflow",
        json={
            "original_prompt": "Fetch user profile for user 123",
            "trace": SAMPLE_TRACE,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "projection" in data
    assert "compounding_chart" in data


def test_optimize_workflow_endpoint_pipes_prompt_analysis():
    """When run_prompt_analysis=true (default), the response contains prompt_analysis."""
    response = client.post(
        "/optimize-workflow",
        json={
            "original_prompt": "Please fetch user profile for user 123",
            "trace": SAMPLE_TRACE,
            "run_prompt_analysis": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "prompt_analysis" in data
    assert data["prompt_analysis"]["efficiency_score"] is not None


def test_optimize_workflow_endpoint_skips_prompt_analysis():
    """When run_prompt_analysis=false, prompt_analysis key should be absent."""
    response = client.post(
        "/optimize-workflow",
        json={
            "original_prompt": "Do X",
            "trace": SAMPLE_TRACE,
            "run_prompt_analysis": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "prompt_analysis" not in data


def test_optimize_workflow_endpoint_rejects_empty_prompt():
    """Empty prompt should return 422."""
    response = client.post(
        "/optimize-workflow",
        json={
            "original_prompt": "   ",
            "trace": SAMPLE_TRACE,
        },
    )
    assert response.status_code == 422


def test_optimize_workflow_endpoint_rejects_empty_trace():
    """Empty trace should return 422."""
    response = client.post(
        "/optimize-workflow",
        json={
            "original_prompt": "Do X",
            "trace": [],
        },
    )
    assert response.status_code == 422
