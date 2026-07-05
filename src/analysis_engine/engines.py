from typing import List, Dict, Any
from src.ai_gateway.tokenizer_helper import _heuristic_token_count
from src.analysis_engine.models import (
    PromptFeatures, WorkflowInference, TokenPrediction,
    ExecutionStep, AnalysisReport
)

class PromptFeatureExtractor:
    @staticmethod
    def extract(prompt: str) -> PromptFeatures:
        length = len(prompt)
        # Naive complexity heuristic based on length and punctuation
        complexity = min(1.0, (length / 500) + (prompt.count("?") * 0.1))

        intent = "information_retrieval"
        if "calculate" in prompt.lower() or "+" in prompt:
            intent = "calculation"
        elif "cancel" in prompt.lower() or "refund" in prompt.lower():
            intent = "support_escalation"

        return PromptFeatures(
            raw_prompt=prompt,
            length_chars=length,
            intent=intent,
            complexity_score=complexity
        )

class WorkflowInferenceEngine:
    @staticmethod
    def infer(features: PromptFeatures) -> WorkflowInference:
        tools = 1 if features.complexity_score > 0.3 else 0
        if features.intent == "calculation":
            tools += 1

        loops = 2 if tools > 0 else 1

        risk = "low"
        if features.intent == "support_escalation":
            risk = "high"

        return WorkflowInference(
            expected_tools=tools,
            expected_loops=loops,
            risk_level=risk
        )

class TokenPredictionEngine:
    @staticmethod
    def predict(features: PromptFeatures, inference: WorkflowInference) -> TokenPrediction:
        base_tokens = _heuristic_token_count(features.raw_prompt)
        tool_tokens = inference.expected_tools * 150
        completion_tokens = 100 * inference.expected_loops

        total = base_tokens + tool_tokens + completion_tokens

        # Exponential cost compounding simulation
        compounding_factor = 1.0 + (inference.expected_loops * 0.1)

        return TokenPrediction(
            prompt_tokens=base_tokens,
            predicted_completion_tokens=completion_tokens,
            predicted_tool_tokens=tool_tokens,
            estimated_total_tokens=int(total * compounding_factor),
            cost_multiplier=round(compounding_factor, 2)
        )

class SyntheticTraceGenerator:
    @staticmethod
    def generate(features: PromptFeatures, inference: WorkflowInference, tokens: TokenPrediction) -> List[ExecutionStep]:
        trace = []

        trace.append(ExecutionStep(
            step_type="user_input",
            description="Prompt Received",
            tokens=tokens.prompt_tokens,
            latency_ms=10.0,
            token_source="heuristic"
        ))

        for i in range(inference.expected_loops):
            trace.append(ExecutionStep(
                step_type="llm_call",
                description=f"LLM Generation Loop {i+1}",
                tokens=100,
                latency_ms=850.0,
                token_source="prediction"
            ))

            if i < inference.expected_tools:
                trace.append(ExecutionStep(
                    step_type="tool_execution",
                    description=f"Synthetic Tool Execution {i+1}",
                    tokens=150,
                    latency_ms=300.0,
                    token_source="prediction"
                ))

        return trace

class ObservabilityEngine:
    @staticmethod
    def evaluate(trace: List[ExecutionStep]) -> Dict[str, Any]:
        total_latency = sum(s.latency_ms for s in trace)
        total_tokens = sum(s.tokens for s in trace)

        return {
            "total_latency_ms": total_latency,
            "total_tokens_used": total_tokens,
            "efficiency_ratio": round(total_tokens / max(1, total_latency), 2),
            "bottleneck": "llm_call" if any(s.latency_ms > 500 for s in trace) else "none"
        }

class CausalDiscoveryEngine:
    @staticmethod
    def analyze(trace: List[ExecutionStep]) -> Dict[str, Any]:
        # Simulated DoWhy/EconML causal effect calculation
        # In a full implementation, this runs actual causal inference on the trace.
        tool_count = sum(1 for s in trace if s.step_type == "tool_execution")

        return {
            "treatment": "tool_usage",
            "outcome": "latency",
            "average_treatment_effect": tool_count * 250.0,
            "recommendation": "Tool usage causally adds 250ms per invocation. Consider combining tools."
        }

class PromptOptimizationEngine:
    @staticmethod
    def optimize(prompt: str, causal_data: Dict[str, Any]) -> str:
        # Re-write the prompt to optimize the identified causal drivers
        if causal_data["average_treatment_effect"] > 0:
            return prompt + "\n\n# OPTIMIZATION: Please combine your tool calls into a single bulk request to reduce latency."
        return prompt + "\n\n# OPTIMIZATION: Output your final answer in concise JSON format."

class AnalysisPipeline:
    @staticmethod
    def run(raw_prompt: str) -> AnalysisReport:
        features = PromptFeatureExtractor.extract(raw_prompt)
        inference = WorkflowInferenceEngine.infer(features)
        token_pred = TokenPredictionEngine.predict(features, inference)
        trace = SyntheticTraceGenerator.generate(features, inference, token_pred)
        obs_metrics = ObservabilityEngine.evaluate(trace)
        causal_data = CausalDiscoveryEngine.analyze(trace)
        opt_prompt = PromptOptimizationEngine.optimize(raw_prompt, causal_data)

        return AnalysisReport(
            original_prompt=raw_prompt,
            features=features,
            inference=inference,
            token_prediction=token_pred,
            synthetic_trace=trace,
            observability_metrics=obs_metrics,
            causal_analysis=causal_data,
            optimized_prompt=opt_prompt
        )
