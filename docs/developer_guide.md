# Developer Guide

This document provides a comprehensive technical reference for the TracerLensAi codebase. It covers the full architecture, every source file, every function, the local development environment, and the deployment pipeline.

---

## 1. Architecture Overview

TracerLensAi is a decoupled, two-tier application utilizing the Gemini Enterprise Agent Platform:

| Tier | Technology | Location |
|---|---|---|
| **Frontend** | Vanilla HTML/CSS/JS | `src/static/` → Firebase Hosting (CDN) |
| **Backend Proxy** | Python FastAPI | `src/main.py` → Cloud Run (serverless) |
| **Agent Logic** | Python ADK | `src/agent.py` → Vertex AI Agent Runtime |
| **Database** | Memory Bank | Native to Agent Platform |
| **LLM** | Google Gemini | Managed by Agent Runtime |

The frontend communicates with the proxy backend through REST API calls. The proxy forwards prompts to the Agent Runtime to invoke the ADK agent. In production, Firebase Hosting serves static assets and proxies API requests to Cloud Run via rewrite rules.

---

## 2. Directory Layout

```text
TracerLensAi/
├── src/
│   ├── main.py                 # FastAPI app proxy — endpoints
│   ├── agent.py                # ADK Agent definition
│   └── static/
│       ├── index.html          # UI shell — sidebar, chat area, header controls
│       ├── causal-agent.js     # Client-side logic — API calls, rendering, state
│       └── styles.css          # Design system — CSS variables, dark/light mode
│
├── tests/
│   ├── conftest.py             # Pytest fixtures — test client
│   ├── test_main.py            # API endpoint tests
│   └── ui_tests/
│       └── test_ui.py          # Playwright browser tests
│
├── terraform/
│   ├── main.tf                 # Provider configuration
│   ├── variables.tf            # Input variables (project_id, region, etc.)
│   ├── cloudrun.tf             # Cloud Run service + public IAM
│   ├── iam.tf                  # Service accounts, WIF, CI/CD permissions
│   ├── storage.tf              # Artifact Registry, GCS, BigQuery dataset
│   ├── causal_mlops.tf         # API enablement, Causal MLOps registry + bucket
│   └── outputs.tf              # Terraform outputs
│
├── .github/workflows/
│   ├── ci.yml                  # PR gate (lint + test)
│   ├── cd.yml                  # Continuous deployment (build → push → deploy)
│   └── uptime.yml              # Health check ping
│
├── helm/tracerlensai/          # Optional GKE Helm chart
├── Dockerfile                  # Multi-stage build (builder + runtime)
├── docker-compose.dev.yml      # Local dev (hot-reload, test runner, UI tests)
├── requirements.txt            # Python dependencies
├── run_tests.sh                # Docker-based dev/test automation
├── deploy_to_gcp.sh            # Manual GCP deployment fallback
├── firebase.json               # Firebase Hosting config + Cloud Run rewrites
└── .firebaserc                 # Firebase project binding
```

---

## 3. Backend — `src/main.py`

The proxy backend is contained in a single FastAPI application file.

### Pydantic Models

| Model | Fields | Purpose |
|---|---|---|
| `PromptRequest` | `prompt`, `causal_reasoning`, `web_search`, `model_name`, `chat_id` | Request body for `POST /analyze-prompt` |

### API Endpoints

#### `GET /` → `read_root()`
Redirects to `/static/index.html`.

#### `GET /health` → `health_check()`
Returns `{"status": "ok"}`. Used by Docker health checks and Cloud Run readiness probes.

#### `POST /analyze-prompt` → `analyze_prompt(req)`
The proxy endpoint (`proxy/main.py`). This function:
1. Receives the prompt from the UI.
2. When `causal_reasoning` is true, prepends the `[[causal:on]]` control marker to the outbound message (the clean prompt is what gets persisted to history).
3. Forwards the message and `chat_id` (session ID) to the Agent Runtime endpoint configured in `AGENT_ENGINE_ENDPOINT`, authenticating with Application Default Credentials.
4. Parses each streamed event: text parts are concatenated, per-call `usage_metadata` token counts are **summed**, and every `causal_*` key found in `event.actions.state_delta` is collected.
5. Returns `response` (the synthesizer's final answer when present), `total_token_count`, `causal_reasoning_steps`, `causal_graph` (`{nodes, edges, critical_path, version}`), and `causal_status`.

---

## 4. Agent Logic — `src/agent.py`

This file defines the Vertex AI Agent tree:
- **`GeneralAssistant`**: the original single agent (Gemini + `BuiltInCodeExecutor`), used for every non-causal message.
- **`root_agent`**: a deterministic `CausalRouterAgent` (see below) that dispatches per message — causal pipeline when the `[[causal:on]]` marker is present, `GeneralAssistant` otherwise.
- **Memory Bank / sessions**: enabled via the shared session service in `src/app_utils/services.py`.

---

## 4b. Causal Reasoning Pathway — `src/causal/`

When the UI's **Causal Reasoning** toggle is on, the agent runs a multi-agent pipeline that (1) decomposes the problem into components and directed causal relations, (2) derives a global pathway (plan) from the graph, (3) executes it step-by-step while recording a change ledger, (4) propagates impact through graph descendants when a step fails, and (5) replans **only the affected subgraph** from the failure point.

### Agent tree

| Agent | Type | LLM calls | Role |
|---|---|---|---|
| `TracerLensAi_Agent` | `CausalRouterAgent` (custom) | 0 | Marker routing + per-turn causal state reset |
| `CausalDecomposer` | `LlmAgent` | 1 | Structured extraction (`output_schema=CausalDecomposition`); after-callback builds the DAG + plan deterministically |
| `CausalExecutorLoop` | `LoopAgent` (`max_iterations=16`) | — | Bounded execute/verify/replan loop |
| `CausalStepExecutor` | `LlmAgent` + `BuiltInCodeExecutor` | 1/step | Executes exactly one step; ends with `OBSERVED:` / `STEP_STATUS:` trailer |
| `CausalStepController` | custom `BaseAgent` | 0 | Verdict parsing, change ledger, `nx.descendants` impact propagation, invalidation, replan request or escalation |
| `CausalReplanner` | `LlmAgent` | ≤1/failure | Skipped unless a replan is requested; replans only the affected subgraph (`output_schema=ReplanResult`); after-callback splices |
| `CausalSynthesizer` | `LlmAgent` | 1 | Final user-facing answer (`output_key=causal_final_answer`) |

**Isolation invariant** (guarded by `tests/test_causal_agents.py`): every `LlmAgent` carries at most one of `{code_executor, output_schema, tools}` — Vertex rejects built-in tools mixed with function declarations, so all deterministic graph work lives in callbacks/custom agents (`src/causal/callbacks.py`, `controller.py`), never in `FunctionTool`s.

### State-key contract

All pipeline state lives in ADK session state under `causal_*` keys (see `src/causal/state_keys.py`): `causal_graph` (UI shape), `causal_graph_full`, `causal_plan`, `causal_steps` (trace lines), `causal_ledger`, `causal_status`, `causal_current_step`, `causal_final_answer`, `causal_budgets`. Every write rides on an event's `actions.state_delta`, which is simultaneously the persistence write (VertexAiSessionService) and the transport the proxy reads.

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `CAUSAL_MAX_STEPS` | 8 | Max executed plan steps per turn |
| `CAUSAL_MAX_REPLANS` | 2 | Max localized replans per turn |
| `CAUSAL_TEXT_FALLBACK` | off | When `1`, the agent also emits results as a fenced ` ```causal-json ` block for proxies that cannot read state deltas |

Per causal turn the LLM budget is `1 (decompose) + ≤max_steps + ≤max_replans + 1 (synthesis)`; routing, verdicts, impact propagation, and plan splicing are deterministic Python (`networkx`).

---

## 5. Frontend — `src/static/`

### `index.html` — UI Layout

The HTML file defines a two-panel layout:
- **Sidebar** (`<aside class="sidebar">`): App logo, navigation, "New chat" button, recent workflows list.
- **Chat Container** (`<main class="chat-container">`): Header bar with model selector, dark mode toggle, web search toggle; messages area; input pill with send button.

### `causal-agent.js` — Client Logic

| Function | Description |
|---|---|
| `sendMessage()` | Reads input, calls `POST /analyze-prompt`, renders the AI response as parsed Markdown |
| `scrollToBottom()` | Scrolls the messages area to the latest message |
| `escapeHtml(unsafe)` | Sanitizes user input to prevent XSS (`&`, `<`, `>`, `"`, `'`) |

### `styles.css` — Design System

The CSS file defines a comprehensive design system using CSS custom properties.

---

## 6. Local Development Environment

### Docker Compose Services (`docker-compose.dev.yml`)

| Service | Container | Purpose |
|---|---|---|
| `tracerlensai-app` | `tracerlensai-dev` | Main app with hot-reload (`uvicorn --reload`). Mounts `./src` as read-only volume. |
| `test-runner` | `tracerlensai-test` | One-shot test container (profile: `test`). |
| `causal-agent-ui-test` | Playwright image | Browser UI tests (profile: `ui-test`). |

---

## 7. Testing

### Test Configuration

- **`pytest.ini`**: Configures pytest with test path and options.
- **`conftest.py`**: Provides the `client` FastAPI `TestClient` instance fixture.

### Test Files

| File | Tests |
|---|---|
| `test_main.py` | `test_health_check` — verifies `/health` returns 200; tests for `/analyze-prompt` |
| `ui_tests/test_ui.py` | Playwright browser test (placeholder) |

---

## 8. Deployment Architecture

### Cloud Run Proxy
Deployed via `.github/workflows/cd.yml`. Serves as the middle layer between Firebase and the Agent Runtime.

### Agent Runtime
Deployed via `agents-cli deploy` in the CD pipeline. Hosts the `agent.py` logic.

### Firebase Hosting (Frontend CDN)
Firebase Hosting serves static files from `src/static/` and proxies unmatched requests to Cloud Run via the `rewrites` block in `firebase.json`.
