"""TracerLensAi - AI Agentic Workflow Evaluator Backend.

Uses the google-genai SDK with Vertex AI backend for Gemini model access,
including native Code Execution support.
"""
import os
from contextlib import asynccontextmanager
from functools import lru_cache

from google import genai
from google.genai import types
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, List

from src.database import (
    init_db, get_chats, get_chat, create_chat,
    add_message, update_chat_tokens, update_chat_title,
)

# ── App Lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    init_db()
    yield

app = FastAPI(title="TracerLensAi", lifespan=lifespan)

# ── Google GenAI Client (Vertex AI backend) ──────────────────────────────────

if "GOOGLE_CREDENTIALS_JSON" in os.environ:
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, 'w') as f:
        f.write(os.environ["GOOGLE_CREDENTIALS_JSON"])
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

@lru_cache(maxsize=1)
def get_genai_client():
    """Create and cache a GenAI client."""
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "icarus-agent-26")
    region = os.getenv("GOOGLE_CLOUD_LOCATION", os.getenv("GOOGLE_CLOUD_REGION", "us-central1"))
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if gemini_api_key:
        return genai.Client(api_key=gemini_api_key)

    return genai.Client(
        vertexai=True,
        project=project_id,
        location=region,
    )


# ── Static Files ─────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="src/static"), name="static")


@app.get("/")
def read_root():
    """Redirect root to the UI."""
    return RedirectResponse(url="/static/index.html")


@app.get("/health")
def health_check():
    """Health probe for Docker / K8s."""
    return {"status": "ok"}

# ── Chat History API ─────────────────────────────────────────────────────────

class NewChatRequest(BaseModel):
    """Request body to create a new chat session."""
    title: str = "New Chat"

@app.get("/api/chats")
def api_get_chats():
    """Return all chat sessions for the sidebar."""
    return get_chats()

@app.get("/api/chats/{chat_id}")
def api_get_chat(chat_id: str):
    """Return a single chat with its full message history."""
    chat = get_chat(chat_id)
    if chat:
        return chat
    return {"error": "Chat not found"}

@app.post("/api/chats")
def api_create_chat(req: NewChatRequest):
    """Create a new empty chat session."""
    chat_id = create_chat(req.title)
    return {"id": chat_id, "title": req.title}


# ── Analyze Prompt Endpoint ──────────────────────────────────────────────────

class PromptRequest(BaseModel):
    """Request body for the main analysis endpoint."""
    prompt: str
    causal_reasoning: bool = False
    web_search: bool = False
    model_name: str = "gemini-2.5-flash"
    chat_id: Optional[str] = None

@app.post("/analyze-prompt")
def analyze_prompt(req: PromptRequest):
    """Generate a response from Gemini, optionally with causal reasoning.

    If a chat_id is provided, the conversation history is prepended as context
    and both the user prompt and AI response are persisted to the database.
    Code Execution is enabled so Gemini can write and run Python code.
    """
    causal_steps: List[str] = []

    # ── Build conversation context from history ──────────────────────────
    history_lines: List[str] = []
    chat_record = None
    if req.chat_id:
        chat_record = get_chat(req.chat_id)
        if chat_record:
            for msg in chat_record.get("messages", []):
                role_label = "User" if msg["role"] == "user" else "AI"
                history_lines.append(f"{role_label}: {msg['content']}")

            # Persist the new user message
            add_message(req.chat_id, "user", req.prompt)

            # Auto-title the chat from the first message
            if len(chat_record.get("messages", [])) == 0:
                short_title = (req.prompt[:30] + "...") if len(req.prompt) > 30 else req.prompt
                update_chat_title(req.chat_id, short_title)

    context_str = "\n".join(history_lines)
    full_prompt = (
        f"Previous conversation history:\n{context_str}\n\nUser: {req.prompt}"
        if context_str
        else req.prompt
    )

    # ── Call Gemini ──────────────────────────────────────────────────────
    try:
        client = get_genai_client()

        # Select tools based on toggle state
        # NOTE: Vertex AI does not allow mixing search + code_execution tools
        if req.web_search:
            tools = [types.Tool(google_search=types.GoogleSearch())]
        else:
            tools = [types.Tool(code_execution=types.ToolCodeExecution())]

        config = types.GenerateContentConfig(tools=tools)

        response = client.models.generate_content(
            model=req.model_name,
            contents=full_prompt,
            config=config,
        )
        response_text = response.text or ""
        usage = response.usage_metadata
        token_count = (
            (usage.prompt_token_count or 0) + (usage.candidates_token_count or 0)
            if usage
            else 0
        )

        # ── Causal Reasoning (second call) ───────────────────────────────
        if req.causal_reasoning:
            causal_prompt = (
                "Analyze the following request using causal inference. "
                "Identify potential confounders, propose a structural causal model, "
                "and estimate treatment effects. Output 3 to 5 concise steps. "
                f"Format each as a bullet point. Request: {req.prompt}"
            )
            causal_resp = client.models.generate_content(
                model=req.model_name,
                contents=causal_prompt,
                config=config,
            )
            causal_steps = [
                line.strip()
                for line in (causal_resp.text or "").split("\n")
                if line.strip()
            ]
            c_usage = causal_resp.usage_metadata
            if c_usage:
                token_count += (c_usage.prompt_token_count or 0) + (c_usage.candidates_token_count or 0)

        # ── Persist AI response ──────────────────────────────────────────
        if req.chat_id:
            add_message(
                req.chat_id, "ai", response_text,
                causal_steps if req.causal_reasoning else None,
            )
            update_chat_tokens(req.chat_id, token_count)

    except Exception as e:
        response_text = f"Error calling Vertex AI: {str(e)}"
        token_count = 0

    # ── Build JSON response ──────────────────────────────────────────────
    response_data = {
        "status": "success",
        "response": response_text,
        "total_token_count": token_count,
    }

    if req.causal_reasoning:
        response_data["causal_reasoning_steps"] = (
            causal_steps if causal_steps
            else ["Failed to generate causal reasoning steps."]
        )

    return response_data
