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
app.mount("/static", StaticFiles(directory="proxy/static"), name="static")

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
    """Proxy the request to the Vertex AI Agent Engine (streaming)."""

    agent_engine_base = os.getenv("AGENT_ENGINE_ENDPOINT")

    if not agent_engine_base:
        # Mock response for local development if Agent Runtime is not configured
        return {
            "status": "success",
            "response": "Agent Proxy configured. (Set AGENT_ENGINE_ENDPOINT to connect to Agent Runtime). Prompt: " + req.prompt,
            "total_token_count": 10,
            "causal_reasoning_steps": ["Causal reasoning mocked in proxy."] if req.causal_reasoning else []
        }

    # Derive the streaming endpoint from the base query URL
    # e.g. .../reasoningEngines/ID:query  →  .../reasoningEngines/ID:streamQuery
    stream_url = agent_engine_base.replace(":query", ":streamQuery")

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

    # ADK AdkApp only registers stream_query (no sync "query" method)
    payload = {
        "class_method": "stream_query",
        "input": {
            "message": req.prompt,
            "user_id": "default-user",
            "session_id": req.chat_id or "default-session",
        }
    }

    collected_text = []
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", stream_url, json=payload, headers=headers) as resp:
                print(f"DEBUG: status_code={resp.status_code}")
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise HTTPException(status_code=resp.status_code, detail=f"Agent Engine error: {body.decode()}")
                
                body_bytes = await resp.aread()
                print(f"DEBUG: body_bytes={body_bytes}")
                
                import io
                lines = io.BytesIO(body_bytes).readlines()
                for line_bytes in lines:
                    line = line_bytes.decode('utf-8').strip()
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json as _json
                        print(f"DEBUG LINE: {line}")
                        event = _json.loads(line)
                        # ADK streams events; grab text from content parts
                        parts = (event.get("content") or {}).get("parts") or []
                        for part in parts:
                            if isinstance(part, dict) and part.get("text"):
                                collected_text.append(part["text"])
                        # Also handle top-level "output" key
                        if event.get("output"):
                            collected_text.append(str(event["output"]))
                    except Exception:
                        # Plain text line
                        collected_text.append(line)

        return {
            "status": "success",
            "response": "".join(collected_text) or "(no response)",
            "total_token_count": 0,
            "causal_reasoning_steps": []
        }
    except httpx.HTTPStatusError as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=e.response.status_code, detail=f"Agent Engine error: {e.response.text}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

