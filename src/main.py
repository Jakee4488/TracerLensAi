import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any

from src.observability.logger import setup_logger, log_agent_metrics
from src.ai_gateway.vertex_client import VertexAIClient
from src.ai_gateway.fallback_manager import FallbackManager
from src.agent_engine.orchestrator import AgentOrchestrator
from src.observability.optimization_engine import evaluate_workflow
from src.observability.prompt_analysis import analyze_prompt
from src.observability.workflow_optimizer import optimize_workflow
from src.observability.causal_engine import CausalOptimizer
from src.observability.graph_manager import GraphitiContextBuilder

app = FastAPI(title="TracerLensAi: AI Agentic Workflow Evaluator")
logger = setup_logger()

# Mount static files
app.mount("/static", StaticFiles(directory="src/static"), name="static")

# Dependency Initialization
project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "default-project")
location = os.environ.get("GOOGLE_CLOUD_REGION", "europe-west2")

_agent = None

def get_agent() -> AgentOrchestrator:
    global _agent
    if _agent is None:
        try:
            client = VertexAIClient(project_id, location)
            fallback_mgr = FallbackManager(client)
            _agent = AgentOrchestrator(fallback_mgr)
        except Exception as e:
            logger.error(f"Failed to initialize AgentOrchestrator: {e}")
            raise RuntimeError("Agent failed to initialize.")
    return _agent

class InquiryRequest(BaseModel):
    user_id: str
    prompt: str

class InquiryResponse(BaseModel):
    response: str
    escalated: bool
    trace: List[Dict[str, Any]]
    metrics: Dict[str, Any]

class EvaluateRequest(BaseModel):
    original_prompt: str
    ideal_steps: int
    trace: List[Dict[str, Any]]


class PromptAnalysisRequest(BaseModel):
    prompt: str


class PromptWorkflowNode(BaseModel):
    id: str
    label: str
    stage: str
    description: str
    tokens: int
    latency_ms: float
    position: Dict[str, int]


class PromptAnalysisResponse(BaseModel):
    original_prompt: str
    simulated_workflow_nodes: List[PromptWorkflowNode]
    efficiency_score: int
    optimization_tips: List[str]
    optimized_prompt: str


class WorkflowOptimizeRequest(BaseModel):
    original_prompt: str
    trace: List[Dict[str, Any]]
    expected_loops: int | None = None
    run_prompt_analysis: bool = True


class CausalOptimizeRequest(BaseModel):
    treatment: str
    outcome: str
    traces: List[Dict[str, Any]]


class CausalOptimizeResponse(BaseModel):
    estimated_effect: float
    treatment_name: str
    outcome_name: str
    recommendation: str


@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse("src/static/index.html")

@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/analyze-prompt", response_model=PromptAnalysisResponse)
async def analyze_prompt_endpoint(request: PromptAnalysisRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=422, detail="Prompt cannot be empty")
    return analyze_prompt(request.prompt)


@app.post("/optimize-workflow")
async def optimize_workflow_endpoint(request: WorkflowOptimizeRequest):
    if not request.original_prompt.strip():
        raise HTTPException(status_code=422, detail="Original prompt cannot be empty")
    if not request.trace:
        raise HTTPException(status_code=422, detail="Trace cannot be empty")

    prompt_analysis_result = None
    if request.run_prompt_analysis:
        prompt_analysis_result = analyze_prompt(request.original_prompt)

    return optimize_workflow(
        original_prompt=request.original_prompt,
        trace=request.trace,
        expected_loops=request.expected_loops,
        prompt_analysis=prompt_analysis_result
    )

@app.post("/inquire-traced", response_model=InquiryResponse)
async def process_inquiry(request: InquiryRequest):
    try:
        agent = get_agent()
        result = await agent.process_inquiry(
            user_id=request.user_id,
            prompt=request.prompt,
            session_history=[]
        )

        # Log metrics
        metrics = result.get("metrics", {})
        metrics["escalated"] = result["escalated"]
        log_agent_metrics(request.user_id, request.prompt, metrics)

        return InquiryResponse(
            response=result["response"],
            escalated=result["escalated"],
            trace=result.get("trace", []),
            metrics=metrics
        )
    except Exception as e:
        logger.error(f"Error processing inquiry: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/evaluate-trace")
async def evaluate_trace(request: EvaluateRequest):
    try:
        agent = get_agent()
        # In a real setup, we might use the agent's LLM to evaluate.
        # For this, we call the optimization engine function.
        evaluation = await evaluate_workflow(
            client=agent.fallback_manager.client,
            trace=request.trace,
            original_prompt=request.original_prompt,
            ideal_steps=request.ideal_steps
        )
        return evaluation
    except Exception as e:
        logger.error(f"Error evaluating trace: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/causal-optimize", response_model=CausalOptimizeResponse)
async def causal_optimize_endpoint(request: CausalOptimizeRequest):
    if not request.traces:
        raise HTTPException(status_code=422, detail="Traces cannot be empty")

    try:
        # 1. Use Graphiti Context Builder to enrich traces
        graph_builder = GraphitiContextBuilder()
        enriched_traces = []
        for trace in request.traces:
            features = graph_builder.extract_graph_features(trace)
            # Merge raw trace with graph features
            enriched_trace = {**trace, **features}
            enriched_traces.append(enriched_trace)

        # 2. Run DoWhy + EconML Causal Engine
        optimizer = CausalOptimizer()

        # We assume Graphiti extraction provides these common causes
        common_causes = ["query_complexity", "system_prompt_size"]

        optimizer.build_causal_model(
            trace_data=enriched_traces,
            treatment=request.treatment,
            outcome=request.outcome,
            common_causes=common_causes
        )

        effect = optimizer.estimate_treatment_effects()
        return CausalOptimizeResponse(**effect)

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error during causal optimization: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during causal estimation")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
