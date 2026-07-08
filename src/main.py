"""TracerLensAi - Lightweight Proxy Backend.

This backend serves the static UI and proxies requests to the
Gemini Enterprise Agent Platform (Agent Runtime).
"""
import os
import httpx
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
    api_key = os.getenv("AGENT_API_KEY", os.getenv("GEMINI_API_KEY"))

    if not agent_engine_url:
        # Mock response for local development if Agent Runtime is not configured
        return {
            "status": "success",
            "response": "Agent Proxy configured. (Set AGENT_ENGINE_ENDPOINT to connect to Agent Runtime). Prompt: " + req.prompt,
            "total_token_count": 10,
            "causal_reasoning_steps": ["Causal reasoning mocked in proxy."] if req.causal_reasoning else []
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "query": req.prompt,
        "session_id": req.chat_id or "default-session",
        "parameters": {
            "causal_reasoning": req.causal_reasoning,
            "web_search": req.web_search,
            "model_name": req.model_name
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(agent_engine_url, json=payload, headers=headers, timeout=60.0)
            resp.raise_for_status()

            agent_data = resp.json()
            return {
                "status": "success",
                "response": agent_data.get("response", ""),
                "total_token_count": agent_data.get("token_count", 0),
                "causal_reasoning_steps": agent_data.get("causal_steps", [])
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
