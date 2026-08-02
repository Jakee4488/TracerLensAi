# Repository Structure

This document provides a detailed breakdown of every directory and file in the TracerLensAi codebase.

For a high-level overview of the project, see the [README](../README.md). For the architecture and API reference, see the [Developer Guide](developer_guide.md).

---

## Top-Level Layout

```text
TracerLensAi/
├── .github/                         # CI/CD pipelines & badges
├── docs/                            # Documentation (this folder)
├── proxy/                           # Cloud Run gateway: FastAPI proxy & compiled frontend
├── ui/                              # React + Vite + TypeScript frontend (source)
├── src/                             # ADK agent: agent logic, causal engine, agent server
├── terraform/                       # GCP Infrastructure as Code
├── tests/                           # Test suite (pytest, Playwright, eval harness)
├── .env.example                     # Environment variable template
├── agents-cli-manifest.yaml         # Config for the Gemini Enterprise Agent Platform (agents-cli)
├── deployment_metadata.json         # Recorded Agent Engine id (kept current by agents-cli)
├── deploy_to_gcp.sh                 # One-step deployment: Agent Engine → Cloud Run → Firebase Hosting
├── docker-compose.yml               # Production-style compose: builds and runs proxy + React UI
├── docker-compose.dev.yml           # Local development environment
├── Dockerfile                       # Container build for the ADK agent server (src/)
├── Dockerfile.proxy                 # Multi-stage: Node 20 builds ui/, Python packages proxy/
├── firebase.json                    # Firebase Hosting configuration + Cloud Run rewrite
├── .firebaserc                      # Firebase CLI project binding (icarus-agent-26)
├── requirements.txt                 # Python dependencies (agent + proxy)
└── requirements-dev.txt             # Dev-only dependencies (Playwright E2E)
```

> **Two backends, one repo.** `src/` is the **ADK agent** deployed to Vertex AI
> Agent Runtime; `proxy/` is the **Cloud Run gateway** the browser talks to.
> They ship as separate container images (`Dockerfile` vs `Dockerfile.proxy`)
> and the proxy image deliberately does **not** bundle `src/` — the small amount
> of shared knowledge (the `[[causal:on]]` marker, the `causal_` key prefix) is
> duplicated in `proxy/main.py` on purpose.

---

## `proxy/` — Cloud Run Gateway

The lightweight FastAPI service the browser calls. It authenticates users,
stores per-user history, accepts file uploads, and proxies chat requests to the
Agent Engine. It never holds API keys — it uses Application Default Credentials.

```text
proxy/
├── main.py                          # FastAPI proxy: auth, history, uploads, agent proxy
└── static/                          # Compiled React bundle (output of ui/ Docker build stage)
    └── index.html                   # Entry point served by the proxy
```

| File | Role |
|---|---|
| `proxy/main.py` | Endpoints: `GET /` (redirect to UI), `GET /health`, `GET /history` & `GET /history/{chat_id}` (Firestore-backed, auth required), `POST /upload` (text file attachments), `POST /analyze-prompt` (prepends the `[[causal:on]]`/`[[web:on]]` markers, streams to the Agent Engine, collects every causal `state_delta` incl. `causal_estimand`/`causal_effect`/`causal_counterfactual`/`causal_graph_reconcile`/`causal_web_retrieval`, sums token usage, persists to Firestore). Optional Firebase auth and CORS allow-list. |
| `proxy/static/` | **Compiled output** of the React + Vite build. Do not edit directly; regenerate with `docker compose up --build` or `cd ui && npm run build`. |

---

## `ui/` — React + Vite Frontend (Source)

The browser UI is a **React 18 + TypeScript + Vite** application. The `Dockerfile.proxy` Node stage runs `npm ci && npm run build` and copies `ui/dist/` to `proxy/static/` inside the container. In production the FastAPI proxy serves these files statically — there is no Node process at runtime.

```text
ui/
├── src/
│   ├── App.tsx                      # Root component: layout, state, send / stop logic
│   ├── main.tsx                     # React entry point (mounts <App />, StrictMode)
│   ├── types.ts                     # Shared TypeScript interfaces (ChatMessage, Report, …)
│   ├── styles.css                   # Design system: tokens, layout, components, animations
│   ├── components/
│   │   ├── ChatHeader.tsx           # Header: token badge, model selector, toggles, auth chip
│   │   ├── Composer.tsx             # Input pill: textarea, attach btn, send/stop button swap
│   │   ├── DropOverlay.tsx          # Full-page drag-and-drop file overlay
│   │   ├── MessageList.tsx          # Message stream, starter cards, pending/live bubble
│   │   ├── Sidebar.tsx              # Brand, conversation history, settings (model/causal/web/theme)
│   │   └── causal/
│   │       ├── CausalPanel.tsx      # Right-pane container (head, estimand, timeline, graph, drawer)
│   │       ├── CausalGraph.tsx      # ReactFlow DAG: nodes by status/kind, click-through to drawer
│   │       ├── EstimandCard.tsx     # DoWhy identification card + EffectChart
│   │       ├── EffectChart.tsx      # Effect estimate bar + CI + refutation rows
│   │       ├── StepDrawer.tsx       # Slide-in overlay: ledger for a specific node
│   │       └── WorkflowTimeline.tsx # Live pipeline stages with elapsed-time counters (rAF)
│   ├── hooks/
│   │   ├── useAttachments.ts        # Attachment upload state machine (chips, upload, remove)
│   │   ├── useHistory.ts            # Firestore conversation history pagination
│   │   └── useRunProgress.ts        # SSE → Stage[] + live CausalGraph state
│   └── lib/
│       ├── api.ts                   # analyzePrompt (SSE + AbortSignal), uploadFile, fetchHistory
│       ├── firebase.ts              # Firebase Auth (watchAuth, getIdToken, sign-in/out)
│       ├── graph.ts                 # buildReactFlowGraph: CausalGraph → ReactFlow + Dagre layout
│       ├── ids.ts                   # generateSessionId, nextMessageKey, getAnonId
│       ├── markdown.ts              # renderMarkdown: marked + DOMPurify + highlight.js
│       ├── sse.ts                   # readSse: async SSE frame generator from a fetch Response
│       ├── stages.ts                # updateStages: ADK progress frames → typed Stage state machine
│       └── theme.ts                 # getTheme / setTheme: localStorage + <html data-theme>
├── package.json                     # React, ReactFlow, Vite, TypeScript deps
└── vite.config.ts                   # Build config: proxy /api → localhost:8080 in dev mode
```

| File | Role |
|---|---|
| `App.tsx` | All top-level React state: messages, input, causal/web/model toggles, `selectedMessageId` for the right pane, `abortControllerRef` for stream cancellation, `isSendingRef` sync lock. Calls `send(overrideText?)` (assembles + streams the request, catches `AbortError` silently) and `stop()` (fires the abort). |
| `Composer.tsx` | Renders the input pill. While `isSending` is true, the `➤` send button is replaced by a red `■` stop button that calls `onStop`. |
| `MessageList.tsx` | User messages, AI markdown bubbles, error rows. When messages are empty: starter prompt cards (call `onPromptClick` which directly invokes `send()`). Pending AI bubble: animated `⚯ Causal reasoning: <active stage>` label while the agent runs; `⚯ View Causal Details →` button on completed causal messages. |
| `WorkflowTimeline.tsx` | Ordered pipeline stage rows; each row's elapsed timer runs its own `requestAnimationFrame` loop (throttled to ~10fps) so ticking never re-renders the parent. |
| `useRunProgress.ts` | Receives `onProgress(frame)` callbacks from `analyzePrompt`; maps `frame.stage` strings to a typed `Stage[]` via `stages.ts`; provides `run.stages` and `run.graph` to `App.tsx`. |
| `api.ts` | `analyzePrompt(body, handlers)` — `POST /analyze-prompt`, reads SSE frames via `readSse`, calls `handlers.onProgress` / `handlers.onGraph`, resolves with the terminal `Report`. Accepts an `AbortSignal` so the fetch can be cancelled mid-stream. |

---

## `src/` — ADK Agent

The agent packaged and deployed to Vertex AI Agent Runtime by `agents-cli`.

```text
src/
├── agent.py                         # Root router + general assistant + engine wrapper
├── fast_api_app.py                  # Agent-side FastAPI server (local/Console serving)
├── app_utils/                       # Shared services & serving surfaces
│   ├── services.py                  #   process-wide session/artifact services
│   ├── a2a.py                       #   Agent2Agent (A2A) route attachment
│   ├── reasoning_engine_adapter.py  #   reasoning_engine {class_method,input} HTTP contract
│   ├── telemetry.py                 #   OpenTelemetry / GenAI logging setup
│   └── typing.py                    #   Feedback pydantic model
└── causal/                          # Causal-reasoning pipeline engine (see below)
```

| File | Role |
|---|---|
| `src/agent.py` | Builds the `general_assistant` (Gemini + `BuiltInCodeExecutor`) and the deterministic `CausalRouterAgent` root; wraps them in an `AdkApp` + `TracerLensEngine` for Agent Runtime. Rewrites `GOOGLE_CLOUD_LOCATION=global` to the working region on import. |
| `src/fast_api_app.py` | Assembles the agent-serving FastAPI app via ADK's `get_fast_api_app`, plus A2A routes, the reasoning-engine adapter, telemetry, and a `/feedback` endpoint. Used for local serving and the Vertex Console Playground. |
| `src/app_utils/services.py` | One shared session + artifact service registered under `shared://` so the ADK web, A2A, and reasoning-engine surfaces all see the same sessions. Picks `VertexAiSessionService`, a URI-configured service, or in-memory. |
| `src/app_utils/a2a.py` | Attaches the A2A JSON-RPC + agent-card endpoints and resolves the public agent-card URL. |
| `src/app_utils/reasoning_engine_adapter.py` | Serves `/api/reasoning_engine` (sync) and `/api/stream_reasoning_engine` (streaming) dispatching to the `AdkApp`'s registered operations. |
| `src/app_utils/telemetry.py` | Configures GenAI prompt/response logging and the Agent Engine tracer provider. |
| `src/app_utils/typing.py` | The `Feedback` pydantic model logged by `/feedback`. |

### `src/causal/` — Causal Reasoning Engine

The deterministic causal pipeline. The **pure** modules have no ADK/Vertex
dependencies (so they unit-test hermetically); the **orchestration** modules
wire them into the ADK multi-agent pipeline. Full walkthrough in
[Causal Reasoning](causal_reasoning.md).

```text
src/causal/
├── state_keys.py                    # [pure] control marker, session-state keys, budgets/caps
├── models.py                        # [pure] pydantic models (graph, plan, ledger, LLM schemas)
├── complexity.py                    # [pure] query-complexity scoring → dynamic budgets
├── graph_engine.py                  # [pure] DAG build/repair, plan, impact propagation, splice
├── runtime.py                       # [pure] verdict parsing, ready-step selection, trace lines
├── ledger.py                        # [pure] append-only, capped change ledger
├── estimation.py                    # [pure] DoWhy identify/estimate/refute + counterfactual + web parsing (lazy dowhy/pandas)
├── discovery.py                     # [pure] data-driven DAG correction (causal-learn PC + LiNGAM; lazy, soft dep)
├── prompts.py                       # instruction providers for each LLM agent
├── callbacks.py                     # deterministic glue between LLM agents and the engine
├── controller.py                    # CausalStepController: the loop's deterministic core
├── estimator.py                     # CausalEstimator: deterministic DoWhy + discovery stage (zero LLM)
├── router.py                        # CausalRouterAgent: marker routing + per-turn state reset
└── agents.py                        # factories wiring the ADK pipeline together (incl. web-search + ingest agents)
```

| File | Role |
|---|---|
| `state_keys.py` | Single source of truth for the `[[causal:on]]` marker, all `causal_*` session keys, budget defaults (`DEFAULT_MAX_STEPS`, `DEFAULT_MAX_REPLANS`), graph caps, and the executor output-trailer regexes. |
| `models.py` | Runtime models (`Component`, `CausalGraph`, `ExecutionPlan`, `PlanStep`, `ChangeRecord`, `CausalStatus`, `IdentificationResult`, `EffectEstimate`, …) and shallow LLM-facing schemas (`CausalDecomposition`, `ReplanResult`, `CausalEstimand`) for constrained decoding; plus `parse_model` and `slugify`. |
| `complexity.py` | Scores a query's complexity from cheap lexical features and maps it to a speed-biased `{max_steps, max_replans}` tier. |
| `graph_engine.py` | `CausalTaskGraph` over `networkx`: validate/repair a DAG, derive the critical path and plan, propagate failure impact to descendants, invalidate steps, and splice localized replans. |
| `runtime.py` | Pure decision helpers: parse the `OBSERVED:`/`STEP_STATUS:` verdict, pick the next ready step, budget checks, and human-readable UI trace lines. |
| `ledger.py` | Append-only change ledger (returns new lists so `state_delta` semantics hold), capped at `LEDGER_CAP`. |
| `estimation.py` | Deterministic DoWhy wrapper: `run_identification` does back-door/IV identification (data-free) and, with a parsed dataset, estimation + refutation; `run_counterfactual` fits a `dowhy.gcm` SCM for do-contrast queries; `acquire_dataframe`/`parse_dataset`/`parse_web_retrieval`/`build_causal_graph` helpers. Lazily imports `dowhy`/`pandas`; never raises. |
| `discovery.py` | Data-driven correction of the LLM-asserted **variable** DAG: `reconcile_graph` runs constraint-based discovery (causal-learn PC, FisherZ) + DirectLiNGAM and conservatively reconciles them against the LLM prior, returning a `GraphReconciliation` (verdict, edits, latent confounders). Lazily imports `causallearn`; a soft dependency that degrades to a no-op (returns `None`) when absent; never raises. |
| `prompts.py` | Callable instruction providers (web-search, decomposer, estimand-spec, executor, replanner, synthesizer) that read bounded state, avoiding template `KeyError`s; `_estimand_grounding` injects the identified estimand (and any reconciliation caution / web evidence). |
| `callbacks.py` | After/before-agent callbacks that build the graph+plan, skip agents on the happy path (incl. `skip_unless_effect_query`, `skip_unless_web_requested`), and splice replans — all writes ride on `state_delta`. |
| `controller.py` | `CausalStepController` (custom `BaseAgent`, zero LLM calls): parses each step's verdict, updates the ledger and graph, requests a replan or escalates, and advances the plan. |
| `estimator.py` | `CausalEstimator` (custom `BaseAgent`, zero LLM): acquires a dataframe (attached or web), runs `discovery.reconcile_graph` (when data is present) then `estimation.run_identification`/`run_counterfactual`, and writes `causal_graph_reconcile`/`causal_estimand`/`causal_effect`/`causal_counterfactual`; a silent no-op when the estimand stage was skipped. |
| `router.py` | `CausalRouterAgent`: routes by marker to the pipeline or the general assistant, records the `[[web:on]]` flag, and seeds complexity-sized budgets while resetting stale causal state each turn. |
| `agents.py` | `build_causal_pipeline()` and `build_root_agent()` factories; the `CausalWebSearch`/`CausalWebIngestor` and `CausalFallbackEmitter` custom agents; enforces the isolation invariant (one built-in per `LlmAgent`). |

---

## `terraform/` — Infrastructure as Code

All GCP resources are declared here using Terraform (hashicorp/google provider `~> 5.0`, Terraform `>= 1.5.0`).

```text
terraform/
├── main.tf                          # Provider configuration + required versions
├── variables.tf                     # Input variables
├── cloudrun.tf                      # Cloud Run service + public IAM
├── iam.tf                           # Service accounts & Workload Identity Federation
├── storage.tf                       # Artifact Registry, GCS, BigQuery
├── causal_mlops.tf                  # API enablement + Causal MLOps resources
└── outputs.tf                       # Terraform outputs
```

| File | Resources Managed |
|---|---|
| `main.tf` | Google provider configuration, required Terraform version (≥1.5.0), provider `~> 5.0` |
| `variables.tf` | `project_id` (`icarus-agent-26`), `region` (`europe-west2`), `github_repo` (`Jakee4488/TracerLensAi`), `causal_artifact_repo_name`, `causal_artifacts_bucket` |
| `cloudrun.tf` | `google_cloud_run_service` (`tracerlensai-app`) with the `agent-app-sa` service account and public `allUsers` invoker IAM; the image is `ignore_changes`d so deploys don't fight Terraform |
| `iam.tf` | `agent-app-sa` (roles: `aiplatform.user`, `bigquery.dataEditor`, `logging.logWriter`), `github-actions-sa` (owner — flagged to scope down), WIF pool `github-actions-pool-v3` + provider `github-actions-provider-v3`, GKE Workload Identity binding, Artifact Registry reader for the compute SA |
| `storage.tf` | Artifact Registry (`agent-docker-repo`), GCS bucket (`<project>-agent-cache`), BigQuery dataset (`agent_orchestrator_logs`, 365-day TTL) |
| `causal_mlops.tf` | API enablement (Artifact Registry, Cloud Functions, Cloud Run, AI Platform), Causal MLOps Artifact Registry (`causal-mlops-repo`), GCS bucket for causal artifacts |
| `outputs.tf` | `artifact_registry_repo` name |

---

## `.github/` — CI/CD & Automation

```text
.github/
├── workflows/
│   ├── ci.yml                       # PR gate — pytest backend suite
│   ├── deploy.yml                   # Continuous deployment (all three tiers)
│   └── uptime.yml                   # Health check & uptime badge
└── badges/
    └── uptime.json                  # shields.io endpoint badge data
```

| Workflow | Trigger | Action |
|---|---|---|
| `ci.yml` | Pull request → `main` | Installs deps and runs `pytest tests/ --ignore=tests/ui_tests` |
| `deploy.yml` | Push to `main` (merge) or manual dispatch | Runs `deploy_to_gcp.sh`: Agent Engine → Cloud Run proxy → Firebase Hosting (dispatch can target a single stage); records a GitHub Deployment linked to `https://tracerlensai.com` |
| `uptime.yml` | Every 5 minutes (cron) | Pings `https://tracerlensai.com/health`, updates `uptime.json` badge |

---

## `tests/` — Test Suite

```text
tests/
├── conftest.py                      # `client` TestClient fixture for the proxy app
├── test_main.py                     # Proxy: health, mock/real analyze-prompt, auth, history, uploads
├── test_main_causal.py              # Proxy: causal + web marker, state_delta / fenced-block transport
├── test_causal_agents.py            # Causal: agent-tree wiring & isolation invariant
├── test_causal_complexity.py        # Causal: complexity scoring → budget tiers
├── test_causal_engine.py            # Causal: graph build/repair, plan, impact, splice
├── test_causal_estimation.py        # Causal: DoWhy identify/estimate/refute, counterfactuals, web parsing
├── test_causal_discovery.py         # Causal: data-driven DAG correction (importorskip causallearn)
├── test_causal_benchmark.py         # Causal: ground-truth ATE recovery over synthetic SCMs
├── test_causal_pipeline_flow.py     # Causal: end-to-end pipeline flow over the engine
├── test_causal_runtime.py           # Causal: verdict parsing & decision helpers
├── ui_tests/
│   ├── conftest.py                  # Playwright fixtures (server, console errors, sample files)
│   └── test_ui.py                   # Browser E2E against the mock-mode proxy
└── eval/
    ├── eval_config.yaml             # agents-cli eval config (LLM-as-judge)
    ├── metrics.py                   # Custom multi-axis causal judge
    └── datasets/
        ├── README.md
        ├── basic-dataset.json       # Eval cases (both pathways)
        └── causal-inference-dataset.json  # Causal-pipeline ground truths (identification, traps, counterfactual)
```

The proxy tests use FastAPI's `TestClient` and a `FakeStore` that mimics
Firestore (no GCP calls). The causal tests exercise the pure engine directly.
UI tests drive the mock-mode proxy in a real browser via Playwright and are
**excluded** from CI (they need a live stack); run them locally with
`requirements-dev.txt`.

---

## Root Configuration Files

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build for the **ADK agent server** (`src/`); runs `uvicorn src.fast_api_app:app`. |
| `Dockerfile.proxy` | Multi-stage build for the **Cloud Run proxy** (`proxy/`); runs `uvicorn proxy.main:app`. |
| `docker-compose.dev.yml` | Services: `tracerlensai-app` (hot-reload agent server), `test-runner` (pytest, `--profile test`), `causal-agent-ui-test` (Playwright, `--profile ui-test`). |
| `requirements.txt` | FastAPI, uvicorn, pydantic, google-genai, google-adk[gcp], google-agents-cli, a2a-sdk, google-cloud-logging, firebase-admin, python-multipart, networkx, dowhy (identification/estimation), causal-learn (DAG discovery), pytest, flake8. |
| `requirements-dev.txt` | Playwright + pytest-playwright (dev-only; kept out of prod images). |
| `agents-cli-manifest.yaml` | Deploy config: `agent_directory: src`, `region: europe-west2`, `deployment_target: agent_runtime`, `session_type: in_memory`. |
| `deployment_metadata.json` | The deployed Agent Engine's `remote_agent_runtime_id`; `deploy_to_gcp.sh` reads it to point the proxy at the engine. |
| `deploy_to_gcp.sh` | One-step deployment: Agent Engine (agents-cli) → Cloud Run proxy (Dockerfile.proxy) → Firebase Hosting; `--only agent\|proxy\|hosting` deploys a single stage. |
| `firebase.json` | Firebase Hosting config: serves `proxy/static/`, rewrites all paths to the `tracerlensai-app` Cloud Run service. |
| `.firebaserc` | Binds the Firebase CLI to project `icarus-agent-26`. |
| `.env.example` | Template for `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_REGION`, `GOOGLE_CLOUD_LOCATION`, optional `GEMINI_API_KEY`, and Artifact Registry vars. |
| `pytest.ini` | Pytest configuration (deprecation-warning filter). |
| `.flake8` | Flake8 linting rules. |
| `.gitignore` | Ignores venvs, terraform state, credentials, Firebase cache, DB files, `.claude`. |
