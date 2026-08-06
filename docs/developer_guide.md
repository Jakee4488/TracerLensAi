# Developer Guide

The technical reference for the codebase: architecture, both backends, the
transport between them, the frontend, local development, testing, and deployment.

For a file-by-file map see [Repository Structure](repository_structure.md); for
the causal engine see [Causal Reasoning](causal_reasoning.md).

---

## 1. Architecture Overview

Two **independently deployed backends** plus a compiled frontend:

| Tier | Technology | Code | Runs on |
|---|---|---|---|
| **Frontend** | React 18 + TypeScript + Vite | `ui/src/` | Compiled to `ui/dist`, served by the proxy |
| **Proxy Gateway** | Python FastAPI | `proxy/` | Cloud Run |
| **ADK Agent** | Python ADK | `src/agent.py`, `src/causal/` | Vertex AI Agent Runtime |
| **Agent Server** | Python FastAPI | `src/fast_api_app.py` | Local / Vertex Console serving |
| **Access + history** | Firestore | `proxy/access.py`, `proxy/main.py` | Native GCP |
| **Session context** | ADK session service | `src/app_utils/services.py` | Agent Engine |
| **LLM** | Gemini (`gemini-2.5-flash` / `gemini-2.5-pro`) | — | Managed by Agent Runtime |

The **browser only ever talks to the proxy** (directly, or through Firebase
Hosting's rewrite). The proxy gates access, stores history, and forwards prompts
to the Agent Runtime with Application Default Credentials — Vertex credentials
never reach the client.

> **Why two `main`-like files?** `proxy/main.py` is the public gateway;
> `src/fast_api_app.py` is the agent's own server. In production they are
> separate images and separate services. The proxy image does not bundle `src/`,
> and installs `requirements-proxy.txt` — which deliberately excludes the ~450 MB
> dowhy/causal-learn stack.

---

## 2. Directory Layout

See [Repository Structure](repository_structure.md) for the full map. In brief:

```text
proxy/     main.py · access.py · admin.py · memstore.py
src/       agent.py · fast_api_app.py · app_utils/ · causal/
ui/src/    App.tsx · components/ · hooks/ · lib/ · styles.css
tests/     pytest suite · ui_tests/ (Playwright) · eval/
```

---

## 3. Proxy Gateway — `proxy/`

### Pydantic models

| Model | Fields |
|---|---|
| `PromptRequest` | `prompt`, `causal_reasoning`, `web_search`, `model_name`, `chat_id`, `attachments`, `run_id` |

### Access and Firestore

Authentication is an **email access gate**, not Firebase Auth. Full design in
[access_control.md](access_control.md).

- `get_caller(authorization)` (`proxy/access.py`) — resolves the caller from an
  HMAC-signed session token. This is the dependency every gated route uses.
- `require_access(user)` — enforces approved status and remaining quota.
- `get_db()` — cached Firestore client for the named database
  (`FIRESTORE_DATABASE_ID`, default `tracerlensai`). `ACCESS_STORE=memory` swaps
  in `proxy/memstore.py` for offline work.
- `_save_exchange(...)` — upserts the user and conversation docs, then appends
  the user + AI message pair under `users/{email_key}/conversations/{chat_id}/messages`.

### Uploads

`POST /upload` takes one text-extractable file, enforces an extension allow-list
and `MAX_UPLOAD_BYTES` (default 5 MB), decodes up to `MAX_ATTACHMENT_TEXT_CHARS`
(200k) of UTF-8, and returns a `file_id`. Storage is a per-instance dict,
optionally mirrored to `UPLOAD_DIR`. Attachments are owner-scoped —
`_resolve_attachments` 404s both unknown ids and other users' ids, so ids aren't
probeable. `_attachment_context` renders contents as
`--- Attached file: NAME ---` blocks prepended to the outbound message.

> **Scaling note (also in the code):** the upload store is per-instance and
> ephemeral. If Cloud Run scales past one instance, swap
> `_put_upload`/`_get_upload` for a GCS-backed implementation keyed by
> `uploads/{email_key}/{file_id}` — the call sites don't change.

### API endpoints

| Method & path | Auth | Description |
|---|---|---|
| `GET /` | — | Serves the compiled UI. |
| `GET /health` | — | `{"status": "ok"}` for Docker/Cloud Run/uptime probes. |
| `POST /auth/login` | — | Request access, or ask for a sign-in link. |
| `POST /auth/exchange` | — | Trade a single-use sign-in nonce for a 30-day session. |
| `GET /access/status` | required | Current status, quota, and usage. |
| `POST /access/extension` | required | Request more tokens. |
| `DELETE /account` | required | Delete the caller's data. |
| `GET /history` | required | The 30 most-recent conversations. |
| `GET /history/{chat_id}` | required | One conversation's messages (404 if not the caller's). |
| `POST /upload` | required | Store a text file, return a `file_id`. |
| `POST /analyze-prompt` | **required** | The chat endpoint. Returns **SSE**, not JSON. |

Plus the admin router (`proxy/admin.py`), all behind password + OTP:
`GET /admin`, `GET /admin/act`, `GET /admin/users`, `GET /admin/runs`,
`GET /admin/pending-count`, `POST /admin/auth/start`, `POST /admin/auth/verify`,
`POST /admin/access/approve`, `POST /admin/access/deny`,
`POST /admin/extension/approve`, `POST /admin/notify/retry`,
`POST /admin/user/delete`, `POST /admin/sweep`.

### `POST /analyze-prompt` flow

1. Gate the caller (`require_access`) and resolve any `attachments`, owner-checked.
2. **Mock path** — if `AGENT_ENGINE_ENDPOINT` is unset, stream a canned response
   (plus a sample causal graph when `causal_reasoning` is true) so the UI is
   developable offline.
3. **Real path** — derive the `:streamQuery` URL, take cached ADC credentials,
   build the outbound message (`{markers} {attachment context}{prompt}`), and
   stream `class_method: "stream_query"` to the Agent Engine.
4. Per streamed event: concatenate text parts, collect every `causal_*` key from
   `actions.state_delta` (camelCase tolerated), and **sum** each
   `usage_metadata` (ADK emits one per LLM call in the turn).
5. Emit SSE frames as the run progresses (below).
6. Prefer the synthesizer's `causal_final_answer` over the raw concatenation,
   persist the exchange best-effort, record token usage, and send `done`.

### The SSE contract

This is the interface between the two backends and the single most important
thing to understand before changing either side. `proxy/main.py` produces it;
`ui/src/lib/sse.ts` and `ui/src/lib/stages.ts` consume it.

Response is `text/event-stream`. Four frame types:

| Frame | Payload | Meaning |
|---|---|---|
| `progress` | `{stage, phase, steps, current_step, elapsed_ms}` | Pipeline advanced. `steps` carries only trace lines not yet sent. |
| `graph` | the `causal_graph` object | The DAG changed — new topology or a node status. |
| `done` | the full report | Terminal success. |
| `error` | `{detail}` | Terminal failure. |

A bare `: ping` comment is emitted every `SSE_PING_INTERVAL_S` (15s) so
intermediaries don't reap a connection during a long silent stage.

**Stage resolution.** `STAGE_BY_AUTHOR` maps the ADK event `author` — the
agent's name — to a UI stage. The root router is instantiated as
`TracerLensAi_Agent`, not `CausalRouterAgent`, so the map is keyed on both.

**Trace-line diffing.** `causal_steps` is a growing list. The proxy forwards only
lines it hasn't sent, comparing content rather than trusting a length high-water
mark — a writer that replaced the list instead of appending would otherwise leave
its new lines at indices already counted as sent, and they would never be
emitted at all.

The `done` report carries: `response`, `run_id`, `total_token_count`,
`input_token_count`, `output_token_count`, `causal_reasoning_steps`,
`causal_graph`, `causal_status`, `causal_estimand`, `causal_effect`,
`causal_counterfactual`, `causal_graph_reconcile`, `causal_web_retrieval`,
`causal_ledger`, `causal_ledger_dropped`, `causal_plan`, `causal_replan_events`.

### CORS

When `CORS_ORIGINS` is set (comma-separated), the proxy enables CORS for those
origins so the app on `tracerlensai.com` (Firebase Hosting, 60s cap) can call
Cloud Run directly (`api.tracerlensai.com`, no cap) for long causal runs. Empty
by default. Auth is a bearer header, not a cookie, so credentials stay off.

---

## 4. ADK Agent — `src/agent.py`

- **`general_assistant`** — an `Agent` (Gemini + `BuiltInCodeExecutor`) for every
  non-causal message. Google Search grounding is deliberately absent: Vertex
  rejects mixing built-in Search with Code Execution.
- **`agent` / `root_agent`** — the deterministic `CausalRouterAgent` from
  `build_root_agent`, named `TracerLensAi_Agent` to keep the A2A agent card and
  traces stable. Dispatches per message on the `[[causal:on]]` marker.
- **`adk_app`** / **`adk_wrapper`** — the `App` and `AdkApp` bound to the shared
  session and artifact services.
- **`TracerLensEngine`** — a thin `set_up` / `query` / `stream_query` wrapper for
  Agent Runtime that pre-creates sessions to avoid `SessionNotFoundError`.

On import the module rewrites `GOOGLE_CLOUD_LOCATION=global` (injected by
`agents-cli`) to `GOOGLE_CLOUD_REGION`, default **`europe-west2`** — the
project's global Gemini quota is exhausted while the regional endpoint is
healthy, and the genai client snapshots this env var at build time.

---

## 5. Causal Reasoning Pathway — `src/causal/`

Summary here; the walkthrough is [Causal Reasoning](causal_reasoning.md).

### Agent tree

| Agent | Type | LLM calls | Role |
|---|---|---|---|
| `TracerLensAi_Agent` | `CausalRouterAgent` (custom) | 0 | Marker routing, per-turn state reset, complexity-sized budgets |
| `CausalWebSearch` | `LlmAgent` + `google_search` | ≤1 | On `[[web:on]]` only: fetches a CSV or evidence |
| `CausalWebIngestor` | custom `BaseAgent` | 0 | Parses search output into `causal_web_*` |
| `CausalDecomposer` | `LlmAgent` | 1 | `output_schema=CausalDecomposition`; after-callback builds DAG + plan |
| `CausalEstimandSpec` | `LlmAgent` | ≤1 | Effect queries only: variable-level DAG + treatment/outcome |
| `CausalEstimator` | custom `BaseAgent` | 0 | DoWhy: DAG correction, identification, estimation, refutation, counterfactuals |
| `CausalExecutorLoop` | `LoopAgent` (max 16) | — | Bounded execute/verify/replan loop |
| `CausalStepExecutor` | `LlmAgent` + `BuiltInCodeExecutor` | 1/step | One step; ends with `OBSERVED:` / `STEP_STATUS:` |
| `CausalStepController` | custom `BaseAgent` | 0 | Verdict parsing, ledger, impact propagation, replan request |
| `CausalReplanner` | `LlmAgent` | ≤1/failure | Replans only the affected subgraph |
| `CausalSynthesizer` | `LlmAgent` | 1 | The final answer (`output_key=causal_final_answer`) |
| `CausalFallbackEmitter` | custom `BaseAgent` | 0 | Fenced `causal-json` block, only under `CAUSAL_TEXT_FALLBACK=1` |

**Isolation invariant** (guarded by `tests/test_causal_agents.py`): every
`LlmAgent` carries at most one of `{code_executor, output_schema, tools}` and
none carry `sub_agents`. Vertex rejects built-in tools mixed with function
declarations, so all deterministic work lives in callbacks and custom
`BaseAgent`s — never in `FunctionTool`s. This is the constraint most likely to
bite you.

### State-key contract

All pipeline state lives under `causal_*` keys (`src/causal/state_keys.py`).
Every write rides on an event's `actions.state_delta`, which is simultaneously
the persistence write and the transport the proxy reads. The proxy duplicates
only the markers, the `causal_` prefix, and the agent names.

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `CAUSAL_MAX_STEPS` | 8 | Ceiling on executed plan steps per turn |
| `CAUSAL_MAX_REPLANS` | 2 | Ceiling on localized replans per turn |
| `CAUSAL_TEXT_FALLBACK` | off | Also emit a fenced `causal-json` block |

---

## 6. Agent Server & Serving Surfaces

`src/fast_api_app.py` builds the agent's FastAPI server via ADK's
`get_fast_api_app` and attaches every contract to one process:

| Surface | Attached by | Endpoints | Consumer |
|---|---|---|---|
| ADK web/api | `get_fast_api_app` | `/run`, `/run_sse`, dev UI | ADK dev server |
| A2A | `attach_a2a_routes` | `/a2a/{app}` JSON-RPC + agent card | A2A clients, Gemini Enterprise |
| reasoning_engine | `attach_reasoning_engine_routes` | `/api/reasoning_engine`, `/api/stream_reasoning_engine` | Vertex Console; the local proxy target |
| Feedback | `collect_feedback` | `POST /feedback` | Structured feedback logging |

`services.py` registers one shared session + artifact service under `shared://`.
It selects `VertexAiSessionService` when `GOOGLE_CLOUD_AGENT_ENGINE_ID` is set, a
URI-configured service when `SESSION_SERVICE_URI` is set, else in-memory.

### Agent-side environment variables

| Variable | Effect |
|---|---|
| `ALLOW_ORIGINS` | CORS allow-list for the **agent** server (distinct from the proxy's `CORS_ORIGINS`) |
| `SESSION_SERVICE_URI` | URI-configured session backend |
| `GOOGLE_CLOUD_AGENT_ENGINE_ID` / `_LOCATION` | Injected by Agent Runtime; selects `VertexAiSessionService` |
| `LOGS_BUCKET_NAME`, `GENAI_TELEMETRY_PATH`, `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY` | Gate GenAI logging |
| `AGENT_VERSION`, `COMMIT_SHA` | Stamped into the agent card and traces |

> `telemetry.py` runs GenAI capture in **`NO_CONTENT`** mode on purpose: prompts
> routinely carry attached CSVs and business context, and the privacy notice
> promises no prompt text is retained. Don't add content logging here.

---

## 7. Frontend — `ui/`

React 18 + TypeScript + Vite, compiled by `npm run build` (or the
`Dockerfile.proxy` Node stage) to `ui/dist`, which the FastAPI proxy serves
directly via `StaticFiles`. There is no Node process in production.

### `ui/src/App.tsx`

| State / Ref | Purpose |
|---|---|
| `messages` | The transcript |
| `input`, `isSending`, `isSendingRef` | Composer state; the ref is a synchronous double-submit lock |
| `abortControllerRef` | Attached to the live SSE fetch; `.abort()` from `stop()` |
| `causal`, `webSearch`, `model` | Toggles and model selection |
| `selectedMessageId` | Whose causal data the pane shows (`"live"` during a run) |
| `tokenTally` | Cumulative tokens across the session |
| `chatIdRef` | Current conversation id |
| `sidebarCollapsed`, `theme` | Chrome state |
| `rightPaneWidth`, `isResizing` | Split-pane drag |
| `isNarrow`, `paneOverlays` | Responsive breakpoints (`useMediaQuery`) |
| `showExtension` | Quota-extension modal |
| `access`, `history`, `run` | `useAccess`, `useHistory`, `useRunProgress` |

`send()` assembles the request, creates an `AbortController`, and calls
`analyzePrompt` with the signal. `stop()` aborts the stream — which the proxy
treats as a completed-and-billed turn for the tokens already burned.

### Components

| Component | Description |
|---|---|
| `AccessGate.tsx` | Sign-in / request-access modal and privacy notice |
| `Sidebar.tsx` | Brand, new chat, history, **model selector, causal and web toggles**, theme |
| `ChatHeader.tsx` | Hamburger, title, token badge, profile menu or login |
| `MessageList.tsx` | Transcript, starter cards, pending bubble, "How this was derived" |
| `Composer.tsx` | Attachment chips, auto-resizing textarea, send/stop toggle |
| `ProfileMenu.tsx` | Account, theme, data deletion |
| `DropOverlay.tsx` | Full-page drag-and-drop overlay |
| `causal/CausalPanel.tsx` | Right-pane container. **Lazily loaded** — it pulls ReactFlow and dagre |
| `causal/CausalGraph.tsx` | Interactive DAG; layout memoised on a topology key, edges on an appearance key |
| `causal/EstimandCard.tsx` | Identification summary: treatment, outcome, adjustment set |
| `causal/EffectChart.tsx` | Effect estimate, CI, refutation rows |
| `causal/PlanView.tsx` | Execution plan and per-step status |
| `causal/WorkflowTimeline.tsx` | Live stage list with elapsed timers |
| `causal/StepDrawer.tsx` | Slide-in click-through ledger |

### Hooks and lib

| Module | Description |
|---|---|
| `hooks/useAccess.ts` | Access-gate state machine, sign-in link handling, status polling |
| `hooks/useAttachments.ts` | Upload state machine |
| `hooks/useHistory.ts` | Conversation list, `loadMore`, `reload` |
| `hooks/useRunProgress.ts` | Accumulates SSE frames into `Stage[]` and the live graph |
| `hooks/useFocusTrap.ts` | Modal focus containment |
| `hooks/useMediaQuery.ts` | Responsive breakpoints |
| `lib/api.ts` | `analyzePrompt` (SSE + `AbortSignal`), `uploadFile`, `fetchHistory`, `fetchConversation`, `authHeaders` |
| `lib/sse.ts` | `readSse` — async generator over the `event:` / `data:` format |
| `lib/stages.ts` | Maps `progress` frames to typed `Stage` objects |
| `lib/graph.ts` | dagre layout, `buildEdges`, `topologyKey`, `edgeAppearanceKey` |
| `lib/markdown.ts` | marked + DOMPurify, highlight.js (explicit language set), `[Node: …]` citation linkifying |
| `lib/causal.ts` | `hasCausalContent` — dependency-free so the panel stays lazy |
| `lib/export.ts` | `downloadRun` — one auditable JSON file per run |
| `lib/access.ts` | Session token storage |
| `lib/ids.ts` | Session, run, message, and anonymous ids |
| `lib/theme.ts` | Light/dark persistence via `<html data-theme>` |

---

## 8. Local Development

### Option A — offline, no GCP, no spend

```bash
MODE=mock docker compose up --build     # http://localhost:8080
```

`MODE` defaults to **`real`**, which calls the live Agent Engine and costs money.
Pass `MODE=mock` explicitly for the offline path.

### Option B — proxy only, no Docker

```bash
cd ui && npm ci && npm run build && cd ..     # the proxy serves ui/dist, not source
ACCESS_STORE=memory ADMIN_TOKEN=local-admin APP_URL=http://localhost:8080 \
  uvicorn proxy.main:app --reload --port 8080
```

All three env vars are needed: without `ACCESS_STORE=memory` the access gate
wants real Firestore credentials, without `APP_URL` the app refuses to boot, and
without `ADMIN_TOKEN` `/admin` returns 503.

### Option C — full stack against a real agent

See [Local Development with Vertex AI Agent](local_development_vertex_agent.md).

### Docker Compose services (`docker-compose.dev.yml`)

| Service | Profile | Purpose |
|---|---|---|
| `tracerlensai-app` | default | Agent server, hot-reload, mounts `./src`, maps local ADC |
| `proxy` | default | The gateway on **8081**, against the local agent |
| `test-runner` | `test` | One-shot pytest container |
| `causal-agent-ui-test` | `ui-test` | Playwright tests against the running app |

> The compose files bind-mount `${APPDATA}` for ADC and are Windows-first. On
> macOS/Linux replace that line with `~/.config/gcloud`.

---

## 9. Testing

`pytest.ini` sets `testpaths = tests`, filters deprecation warnings, and registers
the `logged_out` marker. `tests/conftest.py` provides the `client` TestClient and
the `fake_store` fixture, which uses `proxy/memstore.py` as its Firestore fake.

| File | Covers |
|---|---|
| `tests/test_access.py` | Email gate, sessions, quota, retention, mail transports |
| `tests/test_admin.py` | OTP two-factor, review endpoints, sweep, dashboard injection |
| `tests/test_main.py` | Proxy endpoints, history, uploads, SSE, token accounting incl. abort |
| `tests/test_main_causal.py` | Causal markers, `state_delta` collection, fallback transport |
| `tests/test_causal_*.py` | The pure engine — wiring and isolation, complexity, graph ops, runtime, pipeline flow, DoWhy, discovery, prompts, ground-truth ATE recovery |
| `tests/test_app_entrypoint.py` | The production ASGI entrypoint imports and serves |
| `tests/ui_tests/` | Playwright E2E |

```bash
python -m pytest tests/ --ignore=tests/ui_tests -v   # what CI runs
cd ui && npm run lint && npm run typecheck && npm run build
```

CI also runs `uv lock --check`. **CI does not run the Playwright suite** — it
needs a built bundle and a browser, so it only runs locally.

---

## 10. Known Limitations

Deliberately recorded rather than silently carried:

- **Blocking I/O on the event loop.** Firestore reads and writes on the request
  path are synchronous inside `async def` handlers, on a single-worker container.
  Under concurrency they stall unrelated in-flight SSE streams.
- **The reasoning-engine adapter drives a synchronous generator.**
  `src/app_utils/reasoning_engine_adapter.py` iterates `AdkApp.stream_query`
  inline, so a long turn blocks the agent server's loop.
- **DoWhy and causal-learn run inline.** `CausalEstimator` performs seconds of
  CPU work inside `async def _run_async_impl`.
- **Attachment text is re-sent per step.** The full user message — including up
  to 200k characters of attachment — is injected into every executor step prompt,
  so a long plan multiplies input tokens by the step count.

---

## 11. Deployment

Three stages, from [`deploy_to_gcp.sh`](../deploy_to_gcp.sh):

1. **Agent Engine** — `agents-cli deploy` packages `src/` and updates the Runtime
   in place; `deployment_metadata.json` records the engine id.
2. **Cloud Run proxy** — builds `Dockerfile.proxy`, deploys `tracerlensai-app`,
   points it at the engine and forwards the access-gate configuration.
3. **Firebase Hosting** — builds `ui/` and publishes `ui/dist` with the rewrite
   rule from `firebase.json`.

Full pipeline, environments, secrets, WIF, and DNS:
[Deployment Guide](deployment_guide.md).
