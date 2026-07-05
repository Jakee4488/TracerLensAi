from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class PromptFeatures(BaseModel):
    raw_prompt: str
    length_chars: int
    intent: str
    complexity_score: float  # 0.0 to 1.0

class WorkflowInference(BaseModel):
    expected_tools: int
    expected_loops: int
    risk_level: str

class TokenPrediction(BaseModel):
    prompt_tokens: int
    predicted_completion_tokens: int
    predicted_tool_tokens: int
    estimated_total_tokens: int
    cost_multiplier: float

class ExecutionStep(BaseModel):
    step_type: str
    description: str
    tokens: int
    latency_ms: float
    token_source: str

class AnalysisReport(BaseModel):
    original_prompt: str
    features: PromptFeatures
    inference: WorkflowInference
    token_prediction: TokenPrediction
    synthetic_trace: List[ExecutionStep]
    observability_metrics: Dict[str, Any]
    causal_analysis: Dict[str, Any]
    optimized_prompt: str
