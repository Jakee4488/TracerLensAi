"""TracerLensAi - Lightweight Proxy Backend.

This backend serves the static UI and proxies requests to the
Gemini Enterprise Agent Platform (Agent Runtime).
"""
import os
import httpx
import google.auth
import google.auth.transport.requests
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="TracerLensAi Proxy")

# ── Static Files ─────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="src/static"), name="static")

@app.get("/")
def read_root():
    """Redirect root to the UI."""
    return RedirectResponse(url="/static/index.html")

@app.get("/health")
def health_check():
    """Health probe."""
    return {"status": "ok"}

# ── Agent Proxy API ─────────────────────────────────────────────────────────

class PromptRequest(BaseModel):
    """Request body from the UI."""
    prompt: str
    causal_reasoning: bool = False
    web_search: bool = False
    model_name: str = "gemini-2.5-flash"
    chat_id: Optional[str] = None  # Maps to Agent Session ID

@app.post("/analyze-prompt")
async def analyze_prompt(req: PromptRequest):
    """Proxy the request to the Vertex AI Agent Engine."""

    agent_engine_url = os.getenv("AGENT_ENGINE_ENDPOINT")

    if not agent_engine_url:
        # Mock response for local development if Agent Runtime is not configured
        return {
            "status": "success",
            "response": "Agent Proxy configured. (Set AGENT_ENGINE_ENDPOINT to connect to Agent Runtime). Prompt: " + req.prompt,
            "total_token_count": 10,
            "causal_reasoning_steps": ["Causal reasoning mocked in proxy."] if req.causal_reasoning else []
        }

    # Use Application Default Credentials (ADC) for Vertex AI auth
    try:
        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to obtain ADC credentials: {e}")

    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json"
    }

    # Vertex AI Reasoning Engine expects payload wrapped in {"input": {...}}
    payload = {
        "input": {
            "query": req.prompt,
            "session_id": req.chat_id or "default-session",
            "causal_reasoning": req.causal_reasoning,
            "web_search": req.web_search,
            "model_name": req.model_name
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(agent_engine_url, json=payload, headers=headers, timeout=120.0)
            resp.raise_for_status()

            agent_data = resp.json()

            # The Reasoning Engine wraps the response under "output"
            output = agent_data.get("output", agent_data)

            return {
                "status": "success",
                "response": output.get("response", output.get("text", str(output))),
                "total_token_count": output.get("token_count", output.get("usage", {}).get("total_tokens", 0)),
                "causal_reasoning_steps": output.get("causal_steps", [])
            }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Agent Engine error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
