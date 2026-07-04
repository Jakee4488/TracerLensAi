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

app = FastAPI(title="TraceLens: AI Agentic Workflow Evaluator")
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

class DecisionStep(BaseModel):
    step_type: str
    description: str
    tokens: int
    latency_ms: float

class InquiryResponse(BaseModel):
    response: str
    escalated: bool
    trace: List[DecisionStep]
    metrics: Dict[str, Any]

class EvaluateRequest(BaseModel):
    original_prompt: str
    ideal_steps: int
    trace: List[Dict[str, Any]]

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse("src/static/index.html")

@app.get("/health")
def health_check():
    return {"status": "healthy"}

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
