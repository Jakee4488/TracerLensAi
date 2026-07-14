"""TracerLensAi - Lightweight Proxy Backend.

This backend serves the static UI and proxies requests to the
Gemini Enterprise Agent Platform (Agent Runtime).
"""
import functools
import json
import os
import re
from datetime import datetime, timezone

import httpx
import google.auth
import google.auth.transport.requests
import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import firestore as firebase_firestore
from google.cloud import firestore as gcf
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="TracerLensAi Proxy")

# ── Auth & Firestore ─────────────────────────────────────────────────────────

def _get_firebase_app():
    """Initialize the Firebase Admin app lazily (uses ADC on Cloud Run)."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        return firebase_admin.initialize_app()

@functools.cache
def get_db():
    """Firestore client for per-user conversation history.

    The project's Firestore database is named ``tracerlensai`` (not the
    default ``(default)``), so pass it explicitly. Overridable via
    FIRESTORE_DATABASE_ID for other environments.
    """
    _get_firebase_app()
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "tracerlensai")
    return firebase_firestore.client(database_id=database_id)

async def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """Optional Firebase auth: no header → anonymous (None); bad token → 401."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Malformed Authorization header")
    try:
        _get_firebase_app()
        decoded = firebase_auth.verify_id_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired ID token")
    return {
        "uid": decoded["uid"],
        "email": decoded.get("email"),
        "name": decoded.get("name"),
    }

def _save_exchange(user: dict, chat_id: str, prompt: str, response_text: str, token_count: int):
    """Persist a user/AI message pair under users/{uid}/conversations/{chat_id}."""
    db = get_db()
    now = datetime.now(timezone.utc)

    user_ref = db.collection("users").document(user["uid"])
    user_ref.set({"email": user.get("email"), "name": user.get("name"), "last_seen": now}, merge=True)

    conv_ref = user_ref.collection("conversations").document(chat_id)
    if conv_ref.get().exists:
        conv_ref.update({"updated_at": now, "total_tokens": gcf.Increment(token_count)})
    else:
        conv_ref.set({
            "title": prompt[:50],
            "created_at": now,
            "updated_at": now,
            "total_tokens": token_count,
        })

    messages = conv_ref.collection("messages")
    messages.add({"role": "user", "content": prompt, "created_at": now})
    messages.add({"role": "ai", "content": response_text, "created_at": datetime.now(timezone.utc)})


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

# ── History API ──────────────────────────────────────────────────────────────

@app.get("/history")
async def list_history(user: Optional[dict] = Depends(get_current_user)):
    """List the signed-in user's conversations, most recent first."""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    db = get_db()
    query = (
        db.collection("users").document(user["uid"]).collection("conversations")
        .order_by("updated_at", direction=gcf.Query.DESCENDING)
        .limit(30)
    )
    conversations = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        updated_at = data.get("updated_at")
        conversations.append({
            "chat_id": doc.id,
            "title": data.get("title") or "(untitled)",
            "total_tokens": data.get("total_tokens", 0),
            "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
        })
    return {"conversations": conversations}

@app.get("/history/{chat_id}")
async def get_history(chat_id: str, user: Optional[dict] = Depends(get_current_user)):
    """Return the messages of one of the signed-in user's conversations."""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    db = get_db()
    conv_ref = (
        db.collection("users").document(user["uid"])
        .collection("conversations").document(chat_id)
    )
    snapshot = conv_ref.get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv = snapshot.to_dict() or {}
    messages = [
        {"role": (doc.to_dict() or {}).get("role"), "content": (doc.to_dict() or {}).get("content")}
        for doc in conv_ref.collection("messages").order_by("created_at").stream()
    ]
    return {
        "chat_id": chat_id,
        "title": conv.get("title"),
        "total_tokens": conv.get("total_tokens", 0),
        "messages": messages,
    }

# ── Agent Proxy API ─────────────────────────────────────────────────────────

# Must match src/causal/state_keys.py (the proxy image does not ship src/).
CAUSAL_MODE_MARKER = "[[causal:on]]"
CAUSAL_STATE_PREFIX = "causal_"
_CAUSAL_FENCED_RE = re.compile(r"```causal-json\s*(\{.*?\})\s*```", re.DOTALL)


class PromptRequest(BaseModel):
    """Request body from the UI."""
    prompt: str
    causal_reasoning: bool = False
    web_search: bool = False
    model_name: str = "gemini-2.5-flash"
    chat_id: Optional[str] = None  # Maps to Agent Session ID


def _extract_causal_fallback(text: str):
    """Fallback transport: pull steps/graph out of a fenced ```causal-json```
    block emitted by the agent when CAUSAL_TEXT_FALLBACK=1, and strip the
    block from the visible response."""
    match = _CAUSAL_FENCED_RE.search(text or "")
    if not match:
        return None, text
    cleaned = _CAUSAL_FENCED_RE.sub("", text).strip()
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None, cleaned
    return (payload if isinstance(payload, dict) else None), cleaned

def _persist_if_signed_in(user: Optional[dict], req: "PromptRequest", response_text: str, token_count: int):
    """Best-effort history write; never fails the chat response."""
    if not user or not req.chat_id:
        return
    try:
        _save_exchange(user, req.chat_id, req.prompt, response_text, token_count)
    except Exception as e:
        print(f"WARNING: failed to persist history for uid={user['uid']}: {e}")

@app.post("/analyze-prompt")
async def analyze_prompt(req: PromptRequest, user: Optional[dict] = Depends(get_current_user)):
    """Proxy the request to the Vertex AI Agent Engine (streaming)."""

    agent_engine_base = os.getenv("AGENT_ENGINE_ENDPOINT")

    if not agent_engine_base:
        # Mock response for local development if Agent Runtime is not configured
        mock_text = "Agent Proxy configured. (Set AGENT_ENGINE_ENDPOINT to connect to Agent Runtime). Prompt: " + req.prompt
        _persist_if_signed_in(user, req, mock_text, 10)
        mock_graph = None
        mock_steps = []
        if req.causal_reasoning:
            # Canned graph so the UI panel/diagram is developable offline.
            mock_steps = [
                "[graph] decomposed problem into 3 components, 2 causal links",
                "[plan] Global pathway s1 -> s2 along critical path inputs -> analysis -> outcome",
                "[ok] s1 (analysis): Advance 'Analysis' | observed: mocked in proxy",
            ]
            mock_graph = {
                "nodes": [
                    {"id": "inputs", "label": "Inputs", "kind": "input", "status": "done"},
                    {"id": "analysis", "label": "Analysis", "kind": "process", "status": "done"},
                    {"id": "outcome", "label": "Outcome", "kind": "outcome", "status": "pending"},
                ],
                "edges": [
                    {"source": "inputs", "target": "analysis", "relation": "informs", "confidence": 0.9},
                    {"source": "analysis", "target": "outcome", "relation": "causes", "confidence": 0.8},
                ],
                "critical_path": ["inputs", "analysis", "outcome"],
                "version": 1,
            }
        return {
            "status": "success",
            "response": mock_text,
            "total_token_count": 10,
            "causal_reasoning_steps": mock_steps,
            "causal_graph": mock_graph,
            "causal_status": {"phase": "complete"} if req.causal_reasoning else None,
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

    # Causal mode rides on a per-message control marker the agent's router
    # keys on; the clean prompt (req.prompt) is what gets persisted.
    outbound_message = req.prompt
    if req.causal_reasoning:
        outbound_message = f"{CAUSAL_MODE_MARKER} {req.prompt}"

    # ADK AdkApp only registers stream_query (no sync "query" method)
    payload = {
        "class_method": "stream_query",
        "input": {
            "message": outbound_message,
            "user_id": user["uid"] if user else "default-user",
            "session_id": req.chat_id or "default-session",
        }
    }

    collected_text = []
    causal_state = {}
    total_token_count = 0
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
                        # Causal pipeline results ride on event state deltas:
                        # collect every causal_* key (lists are rewritten
                        # whole by the agent, so last write wins).
                        actions = event.get("actions") or {}
                        delta = actions.get("state_delta") or actions.get("stateDelta") or {}
                        if isinstance(delta, dict):
                            for key, value in delta.items():
                                if isinstance(key, str) and key.startswith(CAUSAL_STATE_PREFIX):
                                    causal_state[key] = value
                        # ADK emits usage metadata once per LLM call within the
                        # turn (snake_case or camelCase depending on
                        # serialization); sum them for the multi-agent total.
                        usage = event.get("usage_metadata") or event.get("usageMetadata")
                        if isinstance(usage, dict):
                            count = usage.get("total_token_count", usage.get("totalTokenCount"))
                            if isinstance(count, int):
                                total_token_count += count
                    except Exception:
                        # Plain text line
                        collected_text.append(line)

        # Prefer the synthesizer's final answer over the raw concatenation —
        # in causal mode the text parts include intermediate pipeline output.
        response_text = causal_state.get("causal_final_answer") or "".join(collected_text)

        causal_steps = causal_state.get("causal_steps") or []
        causal_graph = causal_state.get("causal_graph")
        causal_status = causal_state.get("causal_status")
        if req.causal_reasoning and not causal_state:
            # Fallback transport (agent ran with CAUSAL_TEXT_FALLBACK=1).
            payload_json, response_text = _extract_causal_fallback(response_text)
            if payload_json:
                response_text = payload_json.get("final_answer") or response_text
                causal_steps = payload_json.get("steps") or []
                causal_graph = payload_json.get("graph")
                causal_status = payload_json.get("status")

        response_text = response_text.replace(CAUSAL_MODE_MARKER, "").strip() or "(no response)"
        _persist_if_signed_in(user, req, response_text, total_token_count)
        return {
            "status": "success",
            "response": response_text,
            "total_token_count": total_token_count,
            "causal_reasoning_steps": causal_steps,
            "causal_graph": causal_graph,
            "causal_status": causal_status,
        }
    except httpx.HTTPStatusError as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=e.response.status_code, detail=f"Agent Engine error: {e.response.text}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
