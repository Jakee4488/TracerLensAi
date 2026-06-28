import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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


@app.get("/", response_class=HTMLResponse)
async def read_root():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GCP Agent Orchestrator</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            body {
                margin: 0;
                padding: 0;
                font-family: 'Inter', sans-serif;
                background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
                color: #ffffff;
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100vh;
                overflow: hidden;
            }
            .glass-panel {
                background: rgba(255, 255, 255, 0.05);
                backdrop-filter: blur(15px);
                -webkit-backdrop-filter: blur(15px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 20px;
                padding: 3rem;
                text-align: center;
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
                max-width: 600px;
                width: 90%;
                animation: fadeIn 1s ease-out;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(20px); }
                to { opacity: 1; transform: translateY(0); }
            }
            h1 {
                font-size: 2.5rem;
                margin-bottom: 0.5rem;
                background: -webkit-linear-gradient(#00c6ff, #0072ff);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                font-weight: 600;
            }
            p {
                font-size: 1.1rem;
                color: #b0c4de;
                line-height: 1.6;
                margin-bottom: 2rem;
                font-weight: 300;
            }
            .status-badge {
                display: inline-block;
                padding: 0.5rem 1.2rem;
                border-radius: 50px;
                background: rgba(0, 255, 136, 0.1);
                color: #00ff88;
                border: 1px solid rgba(0, 255, 136, 0.2);
                font-size: 0.9rem;
                font-weight: 600;
                letter-spacing: 0.5px;
                box-shadow: 0 0 15px rgba(0, 255, 136, 0.2);
            }
            .links {
                margin-top: 2rem;
                display: flex;
                justify-content: center;
                gap: 1rem;
            }
            .btn {
                text-decoration: none;
                color: white;
                background: rgba(255, 255, 255, 0.1);
                padding: 0.8rem 1.5rem;
                border-radius: 8px;
                transition: all 0.3s ease;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            .btn:hover {
                background: rgba(255, 255, 255, 0.2);
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.2);
            }
        </style>
    </head>
    <body>
        <div class="glass-panel">
            <h1>Agent Orchestrator</h1>
            <p>Your enterprise-grade AI support routing engine is online. Connected to Google Cloud Vertex AI and ready to process requests.</p>
            <div class="status-badge">● Systems Operational</div>
            <div class="links">
                <a href="/docs" class="btn">View API Docs</a>
                <a href="/health" class="btn">Health Check</a>
            </div>
        </div>
    </body>
    </html>
    """

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
