"""TracerLensAi - Lightweight Proxy Backend.

This backend serves the static UI and proxies requests to the
Gemini Enterprise Agent Platform (Agent Runtime).
"""
import asyncio
import functools
import json
import os
import re
import time
import traceback
import uuid
from datetime import datetime, timezone

import httpx
import google.auth
import google.auth.transport.requests
import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import firestore as firebase_firestore
from google.cloud import firestore as gcf
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

app = FastAPI(title="TracerLensAi Proxy")

# Allow the frontend to call this API cross-origin when it is served from a
# different host than the Cloud Run service — e.g. the app is on
# tracerlensai.com (Firebase Hosting, 60s cap) but hits api.tracerlensai.com
# (Cloud Run direct, no cap) for long causal runs. Comma-separated origins in
# CORS_ORIGINS; empty (default) leaves the middleware inert for same-origin.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST", "OPTIONS"],
        # Auth is a bearer header, not a cookie, so credentials stay off.
        # X-Anon-Id must be listed or the cross-origin path silently drops it
        # and every signed-out caller falls back to the shared identity.
        allow_headers=["Authorization", "Content-Type", "X-Anon-Id"],
        allow_credentials=False,
    )

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

_ANON_ID_RE = re.compile(r"[0-9a-f-]{36}")


def _agent_user_id(user: Optional[dict], anon_id: Optional[str]) -> str:
    """Resolve the agent-side user_id for this caller.

    Signed-in callers key on their verified Firebase uid. Signed-out callers
    key on a browser-generated id so they get their own agent session instead
    of sharing one: session state persists in VertexAiSessionService, so a
    shared id leaks one visitor's conversation and causal_* state to the next.

    The header is client-supplied and therefore spoofable, but it grants only a
    guessed *agent session* — never history, which stays gated by
    verify_id_token and the users/{uid} Firestore path. Unrecognized values
    collapse to a single bucket rather than being trusted verbatim, so a
    malformed or injected value can't become an arbitrary session key.
    """
    if user:
        return user["uid"]
    if anon_id and _ANON_ID_RE.fullmatch(anon_id):
        return f"anon:{anon_id}"
    return "anon:unknown"


def _save_exchange(user: dict, chat_id: str, prompt: str, response_text: str, token_count: int,
                   attachments: Optional[list] = None, causal: Optional[dict] = None):
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
    user_msg = {"role": "user", "content": prompt, "created_at": now}
    if attachments:
        user_msg["attachments"] = attachments
    messages.add(user_msg)
    ai_msg = {"role": "ai", "content": response_text, "created_at": datetime.now(timezone.utc)}
    if causal:
        # Stored under the same key names the UI renderer reads off the live
        # response, so a reloaded turn replays identically (diagram, estimand
        # card, steps) instead of degrading to plain Markdown.
        ai_msg["causal"] = causal
    messages.add(ai_msg)


# ── Static Files ─────────────────────────────────────────────────────────────

def _ui_directory() -> Optional[str]:
    """Locate the built UI bundle.

    The React app builds to ui/dist, which is where both local dev and the E2E
    suite find it, and where Dockerfile.proxy copies its build stage's output.
    Returns None on a fresh checkout that hasn't run `npm run build` yet — the
    API still serves, so backend tests don't need Node.
    """
    for candidate in (os.getenv("UI_DIST"), "ui/dist"):
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


UI_DIR = _ui_directory()
if UI_DIR:
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.get("/")
def read_root():
    """Redirect root to the UI."""
    if not UI_DIR:
        raise HTTPException(
            status_code=503,
            detail="UI bundle not built. Run `npm ci && npm run build` in ui/.")
    return RedirectResponse(url="/static/index.html")

@app.get("/health")
def health_check():
    """Health probe."""
    return {"status": "ok"}

# ── History API ──────────────────────────────────────────────────────────────

@app.get("/history")
async def list_history(
    cursor: Optional[str] = None,
    limit: int = 30,
    user: Optional[dict] = Depends(get_current_user),
):
    """List the signed-in user's conversations, most recent first.

    Pass the previous page's ``next_cursor`` (an ISO-8601 ``updated_at``) to
    page backwards in time. ``next_cursor`` is null on the last page.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    page_size = max(1, min(limit, 100))
    db = get_db()
    query = (
        db.collection("users").document(user["uid"]).collection("conversations")
        .order_by("updated_at", direction=gcf.Query.DESCENDING)
    )
    if cursor:
        try:
            # A '+' in the UTC offset decodes to a space in a query string, so
            # accept the cursor whether or not the caller percent-encoded it.
            after = datetime.fromisoformat(cursor.replace(" ", "+"))
        except ValueError:
            # 400 rather than silently restarting at page 1: a silent reset
            # would make the sidebar's "load older" page forever.
            raise HTTPException(status_code=400, detail="Malformed cursor")
        query = query.start_after({"updated_at": after})
    query = query.limit(page_size)

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
    # A short page means we reached the end; a full one may or may not have
    # more, so hand back a cursor and let the next call settle it.
    next_cursor = conversations[-1]["updated_at"] if len(conversations) == page_size else None
    return {"conversations": conversations, "next_cursor": next_cursor}

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
    messages = []
    for doc in conv_ref.collection("messages").order_by("created_at").stream():
        data = doc.to_dict() or {}
        message = {"role": data.get("role"), "content": data.get("content")}
        # Both are absent on most docs: attachments only on user messages that
        # had them, causal only on AI messages from a causal run.
        if data.get("attachments"):
            message["attachments"] = data["attachments"]
        if data.get("causal"):
            message["causal"] = data["causal"]
        messages.append(message)
    return {
        "chat_id": chat_id,
        "title": conv.get("title"),
        "total_tokens": conv.get("total_tokens", 0),
        "messages": messages,
    }

# ── Upload API ───────────────────────────────────────────────────────────────
#
# Dev-first storage: uploads live in an in-process dict (optionally mirrored to
# UPLOAD_DIR so `uvicorn --reload` restarts keep them). On Cloud Run this store
# is per-instance and ephemeral — if the service ever scales past one instance,
# swap _put_upload/_get_upload for a GCS-backed implementation keyed by
# uploads/{uid}/{file_id}; the call sites don't need to change.

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
MAX_ATTACHMENT_TEXT_CHARS = 200_000
ALLOWED_UPLOAD_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".toml", ".xml", ".html", ".css", ".js", ".ts", ".py", ".java", ".go",
    ".rs", ".c", ".cpp", ".h", ".sh", ".sql", ".log",
}

_uploads: dict = {}


def _upload_dir() -> Optional[str]:
    path = os.getenv("UPLOAD_DIR")
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def _put_upload(record: dict) -> str:
    file_id = uuid.uuid4().hex
    _uploads[file_id] = record
    directory = _upload_dir()
    if directory:
        with open(os.path.join(directory, f"{file_id}.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh)
    return file_id


def _get_upload(file_id: str) -> Optional[dict]:
    record = _uploads.get(file_id)
    if record is not None:
        return record
    directory = _upload_dir()
    if directory and re.fullmatch(r"[0-9a-f]{32}", file_id or ""):
        sidecar = os.path.join(directory, f"{file_id}.json")
        if os.path.exists(sidecar):
            with open(sidecar, encoding="utf-8") as fh:
                record = json.load(fh)
            _uploads[file_id] = record
            return record
    return None


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user: Optional[dict] = Depends(get_current_user)):
    """Accept a text-extractable file and return an id to reference in chat."""
    filename = os.path.basename(file.filename or "").strip() or "upload"
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{extension or filename}'. Allowed: {allowed}",
        )
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )
    text = data.decode("utf-8", errors="replace")[:MAX_ATTACHMENT_TEXT_CHARS]
    file_id = _put_upload({
        "filename": filename,
        "size": len(data),
        "content_type": file.content_type or "text/plain",
        "text": text,
        "owner_uid": user["uid"] if user else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "file_id": file_id,
        "filename": filename,
        "size": len(data),
        "content_type": file.content_type or "text/plain",
        "text_chars": len(text),
    }


def _resolve_attachments(ids: list, user: Optional[dict]) -> list:
    """Look up attachment ids, enforcing uploader-only access.

    Unknown ids and other users' uploads both 404 so ids aren't probeable.
    """
    owner = user["uid"] if user else None
    files = []
    for file_id in ids or []:
        record = _get_upload(file_id)
        if record is None or record.get("owner_uid") != owner:
            raise HTTPException(status_code=404, detail=f"Unknown attachment id: {file_id}")
        files.append(record)
    return files


def _attachment_context(files: list) -> str:
    """Render attached file contents as context blocks for the agent."""
    if not files:
        return ""
    blocks = [
        f"--- Attached file: {f['filename']} ---\n{f['text']}\n--- End of file: {f['filename']} ---"
        for f in files
    ]
    return "\n".join(blocks) + "\n\n"


# ── Agent Proxy API ─────────────────────────────────────────────────────────

# Must match src/causal/state_keys.py (the proxy image does not ship src/).
CAUSAL_MODE_MARKER = "[[causal:on]]"
WEB_MODE_MARKER = "[[web:on]]"
CAUSAL_STATE_PREFIX = "causal_"
_CAUSAL_FENCED_RE = re.compile(r"```causal-json\s*(\{.*?\})\s*```", re.DOTALL)

# ── SSE transport ───────────────────────────────────────────────────────────
# /analyze-prompt streams the causal pipeline's progress instead of buffering
# the whole run. Frames: `progress` (stage + newly appended trace lines),
# `graph` (a to_ui_graph payload, one per graph mutation), `done` (the full
# report), `error`. The `done` payload is a superset of the JSON body this
# endpoint used to return, so history persistence and replay are unchanged.

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Without this, nginx-style intermediaries buffer the whole body and the
    # client sees a single flush at the end — which defeats the point.
    "X-Accel-Buffering": "no",
}

# Emitted when the upstream goes quiet, so an intermediary doesn't reap a
# connection during a slow LLM stage (the decomposer alone can run ~30s).
SSE_PING_INTERVAL_S = 15.0

# Pipeline stages, keyed on the ADK event `author` (the agent's name). That
# field survives Agent Engine serialization: AdkApp.stream_query dumps events
# via model_dump_json(exclude_none=True) and `author` is a non-None str.
STAGE_BY_AUTHOR = {
    "CausalRouterAgent": "route",
    "CausalWebSearch": "web",
    "CausalWebIngestor": "web",
    "CausalDecomposer": "decompose",
    "CausalEstimandSpec": "estimand",
    "CausalEstimator": "estimate",
    "CausalStepExecutor": "execute",
    "CausalStepController": "execute",
    "CausalReplanner": "replan",
    "CausalSynthesizer": "synthesize",
}

# build_graph_and_plan is an after_agent_callback on CausalDecomposer, so its
# state write rides on an event authored "CausalDecomposer". Only the delta
# keys distinguish the graph build from the decomposition that preceded it.
_GRAPH_BUILD_KEYS = ("causal_graph_full", "causal_plan")


def _sse(event: str, data) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _resolve_stage(author: Optional[str], delta: dict) -> Optional[str]:
    """Map an ADK event to a pipeline stage id, or None if it isn't one."""
    stage = STAGE_BY_AUTHOR.get(author or "")
    if stage == "decompose" and any(k in delta for k in _GRAPH_BUILD_KEYS):
        return "graph"
    return stage


# Pacing for the offline mock stream. Small enough that the E2E suite stays
# fast, large enough that a human can watch the stages land. Read per-call so
# unit tests can set MOCK_FRAME_DELAY_S=0 and skip the pacing entirely.
MOCK_FRAME_DELAY_S = 0.15


async def _mock_stream(report: dict, causal: bool):
    """Scripted SSE stream for offline dev and the E2E suite.

    Replays the *shape* of a real run — stages in order, the graph appearing
    mid-run, then its nodes flipping pending -> active -> done — so the
    timeline and the live DAG are exercisable without an Agent Engine. The
    final `done` frame is the same report the endpoint used to return.
    """
    if not causal:
        yield _sse("done", report)
        return

    delay = float(os.getenv("MOCK_FRAME_DELAY_S", MOCK_FRAME_DELAY_S))
    graph = report.get("causal_graph") or {}
    nodes = list(graph.get("nodes") or [])
    # Consumed left-to-right so each trace line lands under the stage that
    # would really have produced it.
    pending_steps = list(report.get("causal_reasoning_steps") or [])
    statuses = {n["id"]: "pending" for n in nodes}
    elapsed = 0

    def _graph_frame():
        return _sse("graph", {**graph, "nodes": [
            {**n, "status": statuses[n["id"]]} for n in nodes]})

    def _take_step():
        return [pending_steps.pop(0)] if pending_steps else []

    # Which stages light up mirrors which ones a real run would: the web branch
    # only when the toggle is on, estimand/estimate only when the report
    # actually carries an identification.
    script = [("route", "decomposing"), ("decompose", "decomposing"),
              ("graph", "executing")]
    if report.get("causal_web_retrieval"):
        script.insert(1, ("web", "decomposing"))
    if report.get("causal_estimand"):
        script += [("estimand", "executing"), ("estimate", "executing")]

    for stage, phase in script:
        await asyncio.sleep(delay)
        elapsed += int(delay * 1000)
        yield _sse("progress", {"stage": stage, "phase": phase,
                                "steps": _take_step(), "current_step": None,
                                "elapsed_ms": elapsed})
        if stage == "graph":
            yield _graph_frame()

    # Walk the DAG so the live animation has real transitions to render. Each
    # node ends at the status the report declares, so the last graph frame and
    # the done frame agree.
    for index, node in enumerate(nodes, start=1):
        for status in ("active", node.get("status") or "done"):
            await asyncio.sleep(delay)
            elapsed += int(delay * 1000)
            statuses[node["id"]] = status
            yield _sse("progress", {
                "stage": "execute", "phase": "executing",
                "steps": _take_step() if status != "active" else [],
                "current_step": {"id": node["id"], "index": index,
                                 "total": len(nodes)},
                "elapsed_ms": elapsed})
            yield _graph_frame()

    await asyncio.sleep(delay)
    elapsed += int(delay * 1000)
    # Anything left over (the mock has more trace lines than nodes) lands on
    # the final stage rather than being dropped.
    yield _sse("progress", {"stage": "synthesize", "phase": "synthesizing",
                            "steps": pending_steps, "current_step": None,
                            "elapsed_ms": elapsed})
    yield _sse("done", report)


class PromptRequest(BaseModel):
    """Request body from the UI."""
    prompt: str
    causal_reasoning: bool = False
    web_search: bool = False
    model_name: str = "gemini-2.5-flash"
    chat_id: Optional[str] = None  # Maps to Agent Session ID
    attachments: list = Field(default_factory=list)  # file_ids from POST /upload


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

def _causal_payload(report: dict) -> Optional[dict]:
    """Pull the causal_* fields out of a response body for persistence.

    Returns None when the turn produced nothing causal, so non-causal messages
    keep their original document shape.
    """
    payload = {key: value for key, value in report.items()
               if key.startswith(CAUSAL_STATE_PREFIX) and value}
    return payload or None


def _persist_if_signed_in(user: Optional[dict], req: "PromptRequest", response_text: str, token_count: int,
                          attachment_names: Optional[list] = None, causal: Optional[dict] = None):
    """Best-effort history write; never fails the chat response."""
    if not user or not req.chat_id:
        return
    try:
        _save_exchange(user, req.chat_id, req.prompt, response_text, token_count,
                       attachment_names, causal)
    except Exception as e:
        print(f"WARNING: failed to persist history for uid={user['uid']}: {e}")

@app.post("/analyze-prompt")
async def analyze_prompt(
    req: PromptRequest,
    user: Optional[dict] = Depends(get_current_user),
    x_anon_id: Optional[str] = Header(None),
):
    """Proxy the request to the Vertex AI Agent Engine (streaming)."""

    agent_engine_base = os.getenv("AGENT_ENGINE_ENDPOINT")
    attachment_files = _resolve_attachments(req.attachments, user)
    attachment_names = [f["filename"] for f in attachment_files]

    if not agent_engine_base:
        # Mock response for local development if Agent Runtime is not configured
        mock_text = "Agent Proxy configured. (Set AGENT_ENGINE_ENDPOINT to connect to Agent Runtime). Prompt: " + req.prompt
        if attachment_names:
            # Deterministic acknowledgment so UI/E2E tests can verify uploads.
            mock_text += f"\n\nAttached files ({len(attachment_names)}): {', '.join(attachment_names)}"
        mock_graph = None
        mock_steps = []
        mock_estimand = None
        mock_effect = None
        mock_reconcile = None
        mock_web = None
        mock_ledger = None
        mock_plan = None
        if req.causal_reasoning:
            # Canned graph so the UI panel/diagram is developable offline.
            mock_steps = [
                "[graph] decomposed problem into 3 components, 2 causal links",
                "[plan] Global pathway s1 -> s2 along critical path inputs -> analysis -> outcome",
                "[ok] s1 (analysis): Advance 'Analysis' | observed: mocked in proxy",
            ]
            if req.web_search:
                mock_steps.insert(0, "[web] fetched 120-row observational dataset (2 sources)")
                mock_web = {
                    "mode": "dataset", "row_count": 120, "n_sources": 2,
                    "evidence": [], "sources": ["https://example.org/a", "https://example.org/b"],
                    "note": "",
                }
            mock_steps.append(
                "[graph-fix] data corrected the DAG: 1 edit — e.g. add season->price")
            mock_reconcile = {
                "verdict": "corrected", "n_changes": 1,
                "changes": [{"kind": "add", "source": "season", "target": "price",
                             "reason": "data supports this edge (missed by the stated graph)"}],
                "corrected_edges": [], "latent_confounders": [], "note": "",
            }
            mock_graph = {
                "nodes": [
                    {"id": "inputs", "label": "Inputs", "kind": "input", "status": "done"},
                    {"id": "analysis", "label": "Analysis", "kind": "process", "status": "done"},
                    {"id": "outcome", "label": "Outcome", "kind": "outcome", "status": "done"},
                ],
                "edges": [
                    {"source": "inputs", "target": "analysis", "relation": "informs", "confidence": 0.9},
                    {"source": "analysis", "target": "outcome", "relation": "causes", "confidence": 0.8},
                ],
                "critical_path": ["inputs", "analysis", "outcome"],
                "version": 1,
            }
            # Canned identification/effect so the estimand card is developable
            # offline too (shapes mirror IdentificationResult/EffectEstimate).
            mock_estimand = {
                "treatment": "price", "outcome": "demand",
                "identifiable": True, "estimand_type": "backdoor",
                "adjustment_set": ["season", "income"], "instruments": [],
                "estimand_expr": "E[demand | do(price)]; adjust for: income, season",
                "note": "",
            }
            mock_effect = {
                "method": "backdoor.linear_regression",
                "point": -1.42, "ci_low": -1.61, "ci_high": -1.23, "n_obs": 120,
                "refutations": [
                    {"method": "random_common_cause", "original_effect": -1.42,
                     "new_effect": -1.40, "passed": True, "p_value": 0.86},
                    {"method": "placebo_treatment_refuter", "original_effect": -1.42,
                     "new_effect": 0.03, "passed": True, "p_value": 0.61},
                ],
                "note": "",
            }
            # Ledger entries back the click-through drawer; `affected` is what
            # makes an invalidation visible without re-running the pipeline.
            mock_ledger = [
                {"seq": 1, "step_id": "s1", "component_id": "analysis",
                 "expected": "Advance 'Analysis'", "observed": "mocked in proxy",
                 "verdict": "success", "affected": [], "plan_version": 1, "ts": ""},
            ]
            mock_plan = {
                "version": 1,
                "steps": [
                    {"id": "s1", "component_id": "analysis", "title": "Advance 'Analysis'",
                     "status": "done"},
                ],
            }
        report = {
            "status": "success",
            "response": mock_text,
            "total_token_count": 10,
            "causal_reasoning_steps": mock_steps,
            "causal_graph": mock_graph,
            "causal_status": {"phase": "complete"} if req.causal_reasoning else None,
            "causal_estimand": mock_estimand,
            "causal_effect": mock_effect,
            "causal_counterfactual": None,
            "causal_graph_reconcile": mock_reconcile,
            "causal_web_retrieval": mock_web,
            "causal_ledger": mock_ledger,
            "causal_plan": mock_plan,
        }
        # Persist from the same dict that goes back to the UI so the offline
        # dev path exercises causal history too.
        _persist_if_signed_in(user, req, mock_text, 10, attachment_names,
                              _causal_payload(report))
        return StreamingResponse(
            _mock_stream(report, req.causal_reasoning),
            media_type="text/event-stream", headers=SSE_HEADERS)

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

    # Attached file contents ride as context blocks ahead of the prompt, and
    # causal mode rides on a per-message control marker the agent's router
    # keys on (marker stays first); the clean prompt (req.prompt) is what
    # gets persisted.
    outbound_message = f"{_attachment_context(attachment_files)}{req.prompt}"
    if req.causal_reasoning:
        markers = CAUSAL_MODE_MARKER
        # Web retrieval is meaningful only inside the causal pipeline (the
        # general path uses a code executor, which cannot mix with Search).
        if req.web_search:
            markers = f"{CAUSAL_MODE_MARKER} {WEB_MODE_MARKER}"
        outbound_message = f"{markers} {outbound_message}"

    # ADK AdkApp only registers stream_query (no sync "query" method)
    payload = {
        "class_method": "stream_query",
        "input": {
            "message": outbound_message,
            "user_id": _agent_user_id(user, x_anon_id),
            # A client that omits chat_id gets a throwaway session rather than
            # joining a shared one.
            "session_id": req.chat_id or uuid.uuid4().hex,
        }
    }

    return StreamingResponse(
        _agent_stream(req, user, attachment_names, stream_url, payload, headers),
        media_type="text/event-stream", headers=SSE_HEADERS)


async def _pump_lines(resp, queue: asyncio.Queue):
    """Feed upstream NDJSON lines into a queue.

    The read runs in its own task so the emitting side can wait on the queue
    with a timeout and send keepalives. Racing a timeout directly against
    ``aiter_lines()`` would cancel a partially-consumed read and corrupt the
    stream; cancelling ``queue.get()`` is safe.
    """
    try:
        async for line in resp.aiter_lines():
            await queue.put(("line", line))
    except Exception as e:  # upstream died mid-stream
        await queue.put(("error", str(e)))
    finally:
        await queue.put(("eof", None))


async def _agent_stream(req: "PromptRequest", user: Optional[dict],
                        attachment_names: list, stream_url: str,
                        payload: dict, headers: dict):
    """Forward the Agent Engine run as SSE, then emit the assembled report."""
    collected_text = []
    causal_state = {}
    total_token_count = 0
    steps_sent = 0
    last_stage = None
    started = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", stream_url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    yield _sse("error", {
                        "detail": f"Agent Engine error: {body.decode()}"})
                    return

                queue: asyncio.Queue = asyncio.Queue()
                pump = asyncio.create_task(_pump_lines(resp, queue))
                try:
                    while True:
                        try:
                            kind, value = await asyncio.wait_for(
                                queue.get(), timeout=SSE_PING_INTERVAL_S)
                        except asyncio.TimeoutError:
                            yield ": ping\n\n"
                            continue
                        if kind == "eof":
                            break
                        if kind == "error":
                            yield _sse("error", {"detail": f"Agent Engine stream failed: {value}"})
                            return

                        line = (value or "").strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError:
                            collected_text.append(line)  # plain text line
                            continue

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
                        if not isinstance(delta, dict):
                            delta = {}
                        for key, state_value in delta.items():
                            if isinstance(key, str) and key.startswith(CAUSAL_STATE_PREFIX):
                                causal_state[key] = state_value
                        # ADK emits usage metadata once per LLM call within the
                        # turn (snake_case or camelCase depending on
                        # serialization); sum them for the multi-agent total.
                        usage = event.get("usage_metadata") or event.get("usageMetadata")
                        if isinstance(usage, dict):
                            count = usage.get("total_token_count", usage.get("totalTokenCount"))
                            if isinstance(count, int):
                                total_token_count += count

                        # ── Forward this event as progress ──────────────────
                        stage = _resolve_stage(event.get("author"), delta)
                        all_steps = causal_state.get("causal_steps") or []
                        # causal_steps is rewritten wholesale each write, so the
                        # newly appended lines are whatever is past the high
                        # water mark (the router resets it to [] on turn start).
                        new_steps = all_steps[steps_sent:]
                        steps_sent = len(all_steps)
                        status = causal_state.get("causal_status")
                        phase = status.get("phase") if isinstance(status, dict) else None

                        if stage or new_steps:
                            if stage:
                                last_stage = stage
                            yield _sse("progress", {
                                "stage": stage or last_stage,
                                "phase": phase,
                                "steps": new_steps,
                                "current_step": causal_state.get("causal_current_step"),
                                "elapsed_ms": int((time.monotonic() - started) * 1000),
                            })

                        if delta.get("causal_graph"):
                            yield _sse("graph", delta["causal_graph"])
                finally:
                    pump.cancel()
    except Exception as e:
        traceback.print_exc()
        yield _sse("error", {"detail": str(e)})
        return

    try:
        # Prefer the synthesizer's final answer over the raw concatenation —
        # in causal mode the text parts include intermediate pipeline output.
        response_text = causal_state.get("causal_final_answer") or "".join(collected_text)

        causal_steps = causal_state.get("causal_steps") or []
        causal_graph = causal_state.get("causal_graph")
        causal_status = causal_state.get("causal_status")
        causal_estimand = causal_state.get("causal_estimand")
        causal_effect = causal_state.get("causal_effect")
        causal_counterfactual = causal_state.get("causal_counterfactual")
        causal_graph_reconcile = causal_state.get("causal_graph_reconcile")
        causal_web_retrieval = causal_state.get("causal_web_retrieval")
        # Harvested off the deltas all along but never forwarded until now.
        # The ledger backs the click-through drawer (ChangeRecord already
        # carries expected/observed/verdict/affected per step); the plan backs
        # the executor step counter.
        causal_ledger = causal_state.get("causal_ledger")
        causal_plan = causal_state.get("causal_plan")
        if req.causal_reasoning and not causal_state:
            # Fallback transport (agent ran with CAUSAL_TEXT_FALLBACK=1).
            payload_json, response_text = _extract_causal_fallback(response_text)
            if payload_json:
                response_text = payload_json.get("final_answer") or response_text
                causal_steps = payload_json.get("steps") or []
                causal_graph = payload_json.get("graph")
                causal_status = payload_json.get("status")
                causal_estimand = payload_json.get("estimand")
                causal_effect = payload_json.get("effect")
                causal_counterfactual = payload_json.get("counterfactual")
                causal_graph_reconcile = payload_json.get("graph_reconcile")
                causal_web_retrieval = payload_json.get("web_retrieval")

        response_text = (response_text.replace(CAUSAL_MODE_MARKER, "")
                         .replace(WEB_MODE_MARKER, "").strip() or "(no response)")
        report = {
            "status": "success",
            "response": response_text,
            "total_token_count": total_token_count,
            "causal_reasoning_steps": causal_steps,
            "causal_graph": causal_graph,
            "causal_status": causal_status,
            "causal_estimand": causal_estimand,
            "causal_effect": causal_effect,
            "causal_counterfactual": causal_counterfactual,
            "causal_graph_reconcile": causal_graph_reconcile,
            "causal_web_retrieval": causal_web_retrieval,
            "causal_ledger": causal_ledger,
            "causal_plan": causal_plan,
        }
        # _causal_payload filters on the causal_ prefix, so the two new keys
        # persist to Firestore and replay through history without extra work.
        _persist_if_signed_in(user, req, response_text, total_token_count, attachment_names,
                              _causal_payload(report))
    except Exception as e:
        traceback.print_exc()
        yield _sse("error", {"detail": str(e)})
        return

    yield _sse("done", report)
