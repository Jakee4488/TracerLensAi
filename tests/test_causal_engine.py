import pytest
from src.observability.causal_engine import CausalOptimizer
from src.observability.graph_manager import GraphitiContextBuilder

@pytest.fixture
def mock_trace_data():
    return [
        {"prompt": "Calculate 2+2 using calculator", "search": 0, "calculator": 1, "query_complexity": 1, "total_token_cost": 100, "success": 1},
        {"prompt": "What is the capital of France?", "search": 1, "calculator": 0, "query_complexity": 1, "total_token_cost": 150, "success": 1},
        {"prompt": "Complex math 234*432 with search", "search": 1, "calculator": 1, "query_complexity": 3, "total_token_cost": 500, "success": 1},
        {"prompt": "Who is the president?", "search": 1, "calculator": 0, "query_complexity": 1, "total_token_cost": 120, "success": 1},
        {"prompt": "Find weather and calculate budget", "search": 1, "calculator": 1, "query_complexity": 2, "total_token_cost": 300, "success": 0},
    ]

def test_graphiti_context_builder(mock_trace_data):
    builder = GraphitiContextBuilder()
    features = builder.extract_graph_features(mock_trace_data[0])
    
    assert features["includes_search_tool"] == 0
    assert features["includes_few_shot"] == 0
    assert features["system_prompt_size"] > 0
    assert features["query_complexity"] == 1
    assert features["total_token_cost"] == 100

def test_causal_optimizer_build(mock_trace_data):
    optimizer = CausalOptimizer()
    
    # We want to know the causal effect of adding 'search' tool on 'total_token_cost'
    model = optimizer.build_causal_model(
        trace_data=mock_trace_data,
        treatment="search",
        outcome="total_token_cost",
        common_causes=["query_complexity", "calculator"]
    )
    
    assert model is not None
    assert model._treatment[0] == "search"
    assert model._outcome[0] == "total_token_cost"

def test_causal_optimizer_estimate(mock_trace_data):
    optimizer = CausalOptimizer()
    optimizer.build_causal_model(
        trace_data=mock_trace_data,
        treatment="search",
        outcome="total_token_cost",
        common_causes=["query_complexity", "calculator"]
    )
    
    effect = optimizer.estimate_treatment_effects()
    
    assert "estimated_effect" in effect
    assert effect["treatment_name"] == "search"
    assert effect["outcome_name"] == "total_token_cost"
    assert isinstance(effect["estimated_effect"], float)
