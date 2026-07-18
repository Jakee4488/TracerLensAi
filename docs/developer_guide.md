# Developer Guide

This document is the comprehensive technical reference for the TracerLensAi codebase: the full architecture, both backends, every serving surface, the causal pipeline, the frontend, local development, testing, and deployment.

For a file-by-file map, see the [Repository Structure Guide](repository_structure.md). For the causal engine specifically, see [Causal Reasoning](causal_reasoning.md).

---

## 1. Architecture Overview

TracerLensAi is a decoupled application with **two independently deployed backends** plus a static frontend:

| Tier | Technology | Code | Runs on |
|---|---|---|---|
| **Frontend** | Vanilla HTML/CSS/JS | `proxy/static/` | Firebase Hosting (CDN) |
| **Proxy Gateway** | Python FastAPI | `proxy/main.py` | Cloud Run (serverless) |
| **ADK Agent** | Python ADK | `src/agent.py`, `src/causal/` | Vertex AI Agent Runtime |
| **Agent Server** | Python FastAPI | `src/fast_api_app.py` | Local / Vertex Console serving |
| **User history** | Firestore | (`proxy/main.py`) | Native GCP |
| **Session context** | ADK session service | `src/app_utils/services.py` | Agent Engine |
| **LLM** | Google Gemini (`gemini-2.5-flash`) | — | Managed by Agent Runtime |

The **browser only ever talks to the proxy** (directly, or through Firebase Hosting's rewrite). The proxy authenticates the user, stores history, and forwards chat requests to the Agent Runtime using Application Default Credentials — Vertex credentials never reach the client. The ADK agent runs the reasoning; `src/fast_api_app.py` is the same agent wrapped as a standalone server for local runs and the Vertex Console Playground.

> **Why two `main`-like files?** `proxy/main.py` is the public gateway;
> `src/fast_api_app.py` is the agent's own server. In production they are
> separate container images and separate services. The proxy image does not
> bundle `src/`.

---

## 2. Directory Layout

```text
TracerLensAi/
├── proxy/
│   ├── main.py                 # Gateway: auth, history, uploads, agent proxy
│   └── static/
│       ├── index.html          # UI shell + Firebase Auth bridge + CDN libs
│       ├── causal-agent.js     # Client logic: chat, uploads, history, Mermaid
│       └── styles.css          # Design system (dark/light)
│
├── src/
│   ├── agent.py                # Root router + general assistant + engine wrapper
│   ├── fast_api_app.py         # Agent-side FastAPI server
│   ├── app_utils/              # services, a2a, reasoning_engine_adapter, telemetry, typing
│   └── causal/                 # Causal-reasoning pipeline engine
│
├── tests/                      # pytest (proxy + causal), Playwright UI, eval harness
├── terraform/                  # GCP IaC
├── .github/workflows/          # ci.yml, deploy.yml, uptime.yml
├── Dockerfile                  # ADK agent server image (src/)
├── Dockerfile.proxy            # Cloud Run proxy image (proxy/)
├── docker-compose.dev.yml      # Local dev (hot-reload, test runner, UI tests)
├── deploy_to_gcp.sh            # One-step GCP deployment
├── firebase.json               # Firebase Hosting config + Cloud Run rewrite
└── requirements.txt            # Python dependencies
```

---

## 3. Proxy Gateway — `proxy/main.py`

A single FastAPI app that the browser calls. It is deliberately lightweight and holds no secrets.

### Pydantic Models

| Model | Fields | Purpose |
|---|---|---|
| `PromptRequest` | `prompt`, `causal_reasoning`, `web_search`, `model_name`, `chat_id`, `attachments` | Request body for `POST /analyze-prompt` |

### Auth & Firestore

- `get_current_user(authorization)` — **optional** Firebase auth: no header → anonymous (`None`); a malformed header or bad token → `401`. Verifies the ID token with `firebase_admin.auth.verify_id_token`.
- `get_db()` — cached Firestore client for the named database (`FIRESTORE_DATABASE_ID`, default `tracerlensai`).
- `_save_exchange(...)` — upserts the user profile and the conversation doc (title, timestamps, incrementing `total_tokens`), then appends the user + AI message pair under `users/{uid}/conversations/{chat_id}/messages`.

### Uploads

`POST /upload` accepts a single text-extractable file, enforces an extension allow-list (`ALLOWED_UPLOAD_EXTENSIONS`) and a size cap (`MAX_UPLOAD_BYTES`, default 5 MB), decodes up to `MAX_ATTACHMENT_TEXT_CHARS` (200k) of UTF-8 text, and returns a `file_id`. Storage is an in-process dict optionally mirrored to `UPLOAD_DIR`. Attachments are owner-scoped — `_resolve_attachments` 404s unknown ids **and** other users' ids so ids aren't probeable. `_attachment_context` renders file contents as `--- Attached file: NAME ---` blocks prepended to the outbound message.

> **Scaling note (in the code):** the upload store is per-instance and ephemeral.
> If Cloud Run ever scales past one instance, swap `_put_upload`/`_get_upload`
> for a GCS-backed implementation keyed by `uploads/{uid}/{file_id}` — the call
> sites don't change.

### API Endpoints

| Method & path | Handler | Auth | Description |
|---|---|---|---|
| `GET /` | `read_root` | — | Redirects to `/static/index.html`. |
| `GET /health` | `health_check` | — | Returns `{"status": "ok"}`; used by Docker/Cloud Run/uptime probes. |
| `GET /history` | `list_history` | required | The signed-in user's 30 most-recent conversations. |
| `GET /history/{chat_id}` | `get_history` | required | Messages of one conversation (404 if not the user's). |
| `POST /upload` | `upload_file` | optional | Store a text file, return a `file_id`. |
| `POST /analyze-prompt` | `analyze_prompt` | optional | The main chat endpoint (below). |

### `POST /analyze-prompt` flow

1. Resolve any `attachments` (owner-checked) and render them as context blocks.
2. **Mock path** — if `AGENT_ENGINE_ENDPOINT` is unset, return a canned response (plus a sample 3-node causal graph when `causal_reasoning` is true) so the UI is developable offline; still persists history for signed-in users.
3. **Real path** — derive the `:streamQuery` URL, obtain ADC credentials, build the outbound message (`{attachment context}{prompt}`, with the `[[causal:on]]` marker prepended when causal mode is on), and stream `class_method: "stream_query"` to the Agent Engine.
4. For each streamed event: concatenate text parts, collect every `causal_*` key from `actions.state_delta` (camelCase tolerated), and **sum** each `usage_metadata.total_token_count` (ADK emits one per LLM call in the turn).
5. Prefer the synthesizer's `causal_final_answer` over the raw concatenation; if causal mode produced no state (agent ran with `CAUSAL_TEXT_FALLBACK=1`), parse the fenced ` ```causal-json ` block instead.
6. Strip the marker, persist the exchange (best-effort — never fails the response), and return `response`, `total_token_count`, `causal_reasoning_steps`, `causal_graph` (`{nodes, edges, critical_path, version}`), and `causal_status`.

### CORS

When `CORS_ORIGINS` is set (comma-separated), the proxy enables CORS for those origins so the app on `tracerlensai.com` (Firebase Hosting, 60s cap) can call the Cloud Run service directly (e.g. `api.tracerlensai.com`, no cap) for long causal runs. Empty by default (same-origin only). Auth is a bearer header, not a cookie, so credentials stay off.

---

## 4. ADK Agent — `src/agent.py`

Defines the agent tree deployed to Agent Runtime:

- **`general_assistant`** — an `Agent` (Gemini + `BuiltInCodeExecutor`) used for every non-causal message. (Google Search grounding is coded but commented out: Vertex rejects mixing built-in Search with Code Execution.)
- **`agent` / `root_agent`** — the deterministic `CausalRouterAgent` built by `build_root_agent`. It dispatches per message: the causal pipeline when the `[[causal:on]]` marker is present, `general_assistant` otherwise.
- **`adk_app`** — the `App` wrapping the root agent; **`adk_wrapper`** an `AdkApp` bound to the shared session/artifact services.
- **`TracerLensEngine`** — a thin wrapper exposing `set_up`, `query`, and `stream_query` for Agent Runtime; it pre-creates sessions to avoid `SessionNotFoundError` and drains the stream into a synchronous response when needed.

On import the module rewrites `GOOGLE_CLOUD_LOCATION=global` (injected by agents-cli) to `GOOGLE_CLOUD_REGION` (default `europe-west2`), because the project's global Gemini quota is exhausted while the regional endpoint is healthy — and the genai client snapshots this env var at build time.

---

## 5. Causal Reasoning Pathway — `src/causal/`

When the UI's **Causal Reasoning** toggle is on, the agent runs a multi-agent pipeline that (1) decomposes the problem into components and directed causal relations, (2) derives a global pathway (plan) from the graph, (3) executes it step-by-step while recording a change ledger, (4) propagates impact through graph descendants when a step fails, and (5) replans **only the affected subgraph**. Full walkthrough in [Causal Reasoning](causal_reasoning.md); summary here.

### Agent tree

| Agent | Type | LLM calls | Role |
|---|---|---|---|
| `TracerLensAi_Agent` | `CausalRouterAgent` (custom) | 0 | Marker routing + per-turn causal state reset + complexity-sized budgets |
| `CausalDecomposer` | `LlmAgent` | 1 | Structured extraction (`output_schema=CausalDecomposition`); after-callback builds the DAG + plan deterministically |
| `CausalExecutorLoop` | `LoopAgent` (`max_iterations=16`) | — | Bounded execute/verify/replan loop |
| `CausalStepExecutor` | `LlmAgent` + `BuiltInCodeExecutor` | 1/step | Executes exactly one step; ends with an `OBSERVED:` / `STEP_STATUS:` trailer |
| `CausalStepController` | custom `BaseAgent` | 0 | Verdict parsing, change ledger, `nx.descendants` impact propagation, invalidation, replan request or escalation |
| `CausalReplanner` | `LlmAgent` | ≤1/failure | Skipped unless a replan is requested; replans only the affected subgraph (`output_schema=ReplanResult`); after-callback splices |
| `CausalSynthesizer` | `LlmAgent` | 1 | Final user-facing answer (`output_key=causal_final_answer`) |
| `CausalFallbackEmitter` | custom `BaseAgent` | 0 | Emits results as a fenced `causal-json` block only when `CAUSAL_TEXT_FALLBACK=1` |

**Isolation invariant** (guarded by `tests/test_causal_agents.py`): every `LlmAgent` carries at most one of `{code_executor, output_schema, tools}` and none carry `sub_agents` — Vertex rejects built-in tools mixed with function declarations, so all deterministic graph work lives in callbacks/custom agents (`callbacks.py`, `controller.py`), never in `FunctionTool`s.

### State-key contract

All pipeline state lives in ADK session state under `causal_*` keys (`src/causal/state_keys.py`): `causal_graph` (UI shape), `causal_graph_full`, `causal_plan`, `causal_steps` (trace lines), `causal_ledger`, `causal_status`, `causal_current_step`, `causal_final_answer`, `causal_budgets`, plus internal handoff keys. Every write rides on an event's `actions.state_delta`, which is simultaneously the persistence write and the transport the proxy reads. The proxy duplicates only the marker and the `causal_` prefix (it doesn't ship `src/`).

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `CAUSAL_MAX_STEPS` | 8 | Ceiling on executed plan steps per turn (clamps the dynamic budget) |
| `CAUSAL_MAX_REPLANS` | 2 | Ceiling on localized replans per turn |
| `CAUSAL_TEXT_FALLBACK` | off | When `1`, also emit results as a fenced ` ```causal-json ` block for proxies that can't read state deltas |

Per causal turn the LLM budget is `1 (decompose) + ≤max_steps + ≤max_replans + 1 (synthesis)`; routing, verdicts, impact propagation, and plan splicing are deterministic Python (`networkx`). The **actual** per-query budget is sized by `complexity.py` (simple → very_complex) and then clamped by the env ceilings above.

---

## 6. Agent Server & Serving Surfaces — `src/fast_api_app.py`, `src/app_utils/`

`src/fast_api_app.py` builds the agent's own FastAPI server via ADK's `get_fast_api_app` and attaches extra surfaces so one process serves every contract:

| Surface | Attached by | Endpoints | Consumer |
|---|---|---|---|
| ADK web/api | `get_fast_api_app` | `/run`, `/run_sse`, dev UI, … | ADK dev server / web UI |
| A2A (Agent2Agent) | `attach_a2a_routes` | `/a2a/{app}` JSON-RPC + agent card | A2A clients, Gemini Enterprise |
| reasoning_engine | `attach_reasoning_engine_routes` | `/api/reasoning_engine`, `/api/stream_reasoning_engine` | Vertex Console Playground; the local proxy target |
| Feedback | `collect_feedback` | `POST /feedback` | Structured feedback logging |

`src/app_utils/services.py` registers **one** shared session + artifact service under `shared://` so all surfaces see the same sessions. It selects `VertexAiSessionService` when `GOOGLE_CLOUD_AGENT_ENGINE_ID` is set, a URI-configured service when `SESSION_SERVICE_URI` is set, else in-memory. `telemetry.py` wires GenAI logging and the Agent Engine tracer provider (both opt-in via env). `a2a.py` resolves the public agent-card URL from `APP_URL` or the runtime-injected engine id.

---

## 7. Frontend — `proxy/static/`

### `index.html` — UI shell

- **Sidebar** (`#sidebar`): brand, "New chat" button, recent-workflows history list.
- **Header** (`.chat-header`): sidebar toggle, token badge, model selector (`gemini-2.5-flash` / `-pro`), **Causal** and **Web** toggles, theme toggle, and the Google Sign-In / user chip.
- **Chat** (`.messages` + `.composer`): messages area, attachment chips, input pill with attach + send, and a full-page drag-and-drop overlay for uploads.
- Loads `marked`, `DOMPurify`, `highlight.js`, and `mermaid` from CDNs, applies the saved theme before first paint, and defines the Firebase Auth bridge `window.tracerAuth` (Google popup sign-in, ID-token accessor, auth-state listeners). `window.TRACERLENS_API_BASE` points the client at the Cloud Run URL directly to bypass Hosting's 60s cap.

### `causal-agent.js` — client logic

Grouped by concern:

| Area | Key functions |
|---|---|
| Theme | `applyTheme` (persists to `localStorage`, re-themes Mermaid) |
| Markdown | `renderMarkdown` (marked → **DOMPurify sanitize**, escaped fallback), `highlightCode`, `escapeHtml`, `parseJsonResponse` (tolerates non-JSON gateway errors) |
| Messages | `addUserMessage`, `addAiMessage`, `showTyping`, `addErrorMessage`, `addGreeting`, `scrollToBottom` |
| Causal panel | `buildCausalPanel` (phase badge, step list, graph card + legend) |
| Causal graph | `buildMermaidFlowchart`, `renderCausalGraph`, `sanitizeGraphId`, `sanitizeGraphLabel` (LLM output is untrusted → whitelist-sanitized before Mermaid) |
| Uploads | `handleFiles`, `uploadFile`, `chipNode`, `removeAttachment`, drag-depth overlay handlers |
| Send flow | `sendMessage` (posts `/analyze-prompt` with toggles, model, `chat_id`, attachment ids; updates token badge) |
| Auth + history | `authHeaders` (bearer token), `loadHistoryList`, `renderHistoryList`, `loadConversation` |

Security posture: **all** model/markdown output is sanitized by DOMPurify; Mermaid runs with `securityLevel: "strict"`; graph ids/labels are whitelist-reduced. A Playwright test (`test_markdown_is_sanitized`) probes the XSS path.

### `styles.css` — design system

CSS custom properties for the neon-dark/light themes, the causal panel and graph legend, the composer/input pill, attachment chips, the drop overlay, and responsive layout.

---

## 8. Local Development Environment

### Option A — proxy against the mock agent (no GCP)

```bash
uvicorn proxy.main:app --reload --port 8080   # AGENT_ENGINE_ENDPOINT unset → mock
```

### Option B — full stack (proxy + real ADK agent)

Run the agent server in Docker Compose and point the proxy at its streaming endpoint. See [Local Development with Vertex AI Agent](local_development_vertex_agent.md).

### Docker Compose services (`docker-compose.dev.yml`)

| Service | Profile | Purpose |
|---|---|---|
| `tracerlensai-app` | default | Agent server with hot-reload (`uvicorn src.fast_api_app:app --reload`); mounts `./src`, maps local ADC. |
| `test-runner` | `test` | One-shot pytest container. |
| `causal-agent-ui-test` | `ui-test` | Playwright browser tests against the running app. |

---

## 9. Testing

- **`pytest.ini`** — filters deprecation warnings.
- **`conftest.py`** — the `client` FastAPI `TestClient` fixture for the proxy app.

| File | What it covers |
|---|---|
| `tests/test_main.py` | Health, mock + real `/analyze-prompt`, token summing, auth (401 paths), Firestore history (via a `FakeStore`), uploads (415/413, path-traversal, ownership), attachment persistence & context injection |
| `tests/test_main_causal.py` | Causal marker prepend, `state_delta` collection (snake/camel case), fenced-block fallback, mock-path canned graph |
| `tests/test_causal_*.py` | The pure causal engine: agent wiring & isolation, complexity tiers, graph build/repair/plan/impact/splice, runtime verdict parsing, end-to-end pipeline flow |
| `tests/ui_tests/test_ui.py` | Playwright E2E against the mock proxy: page load, theme persistence, mock round-trip, causal graph render, upload flow, sanitization |

CI (`ci.yml`) runs the pytest suite excluding `ui_tests` (they need a live browser stack and are run locally with `requirements-dev.txt`).

---

## 10. Deployment Architecture

Deployed by [`deploy_to_gcp.sh`](../deploy_to_gcp.sh) (locally or via `.github/workflows/deploy.yml`) in three stages:

1. **Agent Engine** — `agents-cli deploy` packages `src/` and updates the Vertex AI Agent Runtime in place; `deployment_metadata.json` records the engine id.
2. **Cloud Run proxy** — builds `Dockerfile.proxy`, pushes to GCR, deploys `tracerlensai-app`, and points it at the engine (`AGENT_ENGINE_ENDPOINT`, derived from `deployment_metadata.json` if unset) with the `CORS_ORIGINS` allow-list.
3. **Firebase Hosting** — publishes `proxy/static/` with the rewrite rule in `firebase.json` that routes all non-static paths to the Cloud Run proxy.

See the [Deployment Guide](deployment_guide.md) and [Advanced Deployment](advanced_deployment.md) for the full pipeline, WIF setup, and DNS.
