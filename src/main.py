import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.observability.logger import setup_logger, log_agent_metrics
from src.ai_gateway.vertex_client import VertexAIClient
from src.ai_gateway.fallback_manager import FallbackManager
from src.agent_engine.orchestrator import AgentOrchestrator

app = FastAPI(title="Agentic Customer Support Orchestrator")
logger = setup_logger()

# Dependency Initialization
project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "default-project")
location = os.environ.get("GOOGLE_CLOUD_REGION", "europe-west2")

# We defer initialization of the Vertex client until first request to avoid crash if ADC is missing at import time
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

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/inquire", response_model=InquiryResponse)
async def process_inquiry(request: InquiryRequest):
    try:
        agent = get_agent()
        
        # Process the logic
        result = await agent.process_inquiry(
            user_id=request.user_id,
            prompt=request.prompt,
            session_history=[]
        )
        
        # Log metrics to Cloud Logging / BigQuery sync
        metrics = result.get("metrics", {})
        metrics["escalated"] = result["escalated"]
        log_agent_metrics(request.user_id, request.prompt, metrics)
        
        return InquiryResponse(
            response=result["response"],
            escalated=result["escalated"]
        )
        
    except Exception as e:
        logger.error(f"Error processing inquiry: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
