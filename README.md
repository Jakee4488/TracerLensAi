# TracerLensAi

🌐 **[Live Application](https://tracerlensai.com/)** · ![Build](https://github.com/Jakee4488/TracerLensAi/actions/workflows/deploy.yml/badge.svg)

**TracerLensAi** is a cloud-native AI chat interface built on the **Gemini Enterprise Agent Platform**. It pairs a general-purpose Gemini assistant with a deterministic **causal-reasoning pipeline** that decomposes a problem into a causal graph, formally identifies any treatment effect with DoWhy (correcting the graph against real data via causal discovery when a dataset is present), plans and executes it step-by-step, propagates the impact of failures through the graph, and replans only the affected subgraph — all rendered live in the UI as a Mermaid diagram. It can pull observational data or evidence from the web to ground that analysis. The stack is a production-grade GCP deployment: an ADK agent on Vertex AI Agent Runtime, a lightweight FastAPI proxy on Cloud Run, Firebase Auth + Firestore for per-user history, and Firebase Hosting on a custom domain.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Causal Reasoning Pipeline** | A multi-agent ADK pipeline (decompose → identify → execute → replan → synthesize) with deterministic graph engineering in Python. See [Causal Reasoning](docs/causal_reasoning.md). |
| **Formal Identification (DoWhy)** | For treatment-effect questions, a deterministic DoWhy stage identifies the back-door/IV adjustment set from the variable DAG (0 LLM calls) and, when a dataset is present, estimates the effect, runs refutation tests, and computes counterfactuals — so the numeric answer is grounded in a real adjustment set, not the LLM's guess. |
| **Data-Driven DAG Correction** | With a dataset available, causal discovery (causal-learn PC + DirectLiNGAM) conservatively corrects the LLM-asserted graph — reversing, dropping, or adding edges only when the data disagrees strongly and directionally — so a wrong edge changes the *answer*, not just an annotation. |
| **Web Data & Evidence Retrieval** | An optional **Web** toggle lets the causal pipeline fetch best-effort observational data (a CSV) or supporting evidence from the web to feed identification, estimation, and DAG correction. |
| **Live Split-Pane UI** | While the agent is running, the chat area and the causal right-pane update side-by-side in real time. The live bubble shows the active pipeline stage (e.g. `⚯ Causal reasoning: Executing steps…`) with an animated spinner; the right pane renders the full `WorkflowTimeline`, `CausalGraph` (ReactFlow DAG), `EstimandCard`, and `StepDrawer` click-through details. |
| **Interrupt / Stop Button** | A red `■ Stop` button replaces the send button while a run is in-flight; clicking it aborts the SSE stream via the browser's `AbortController` API, immediately stopping execution on both client and server. |
| **Live Causal Graph** | The reasoning graph (nodes, causal edges, critical path, per-step status) streams to the browser and renders as an interactive ReactFlow diagram with a status legend, an identification card, and web/graph-fix badges. |
| **React + Vite Frontend** | The UI is a production-grade React 18 + TypeScript + Vite application (`ui/`) compiled to a static bundle and served by the FastAPI proxy. Dark/light theme toggle, Firebase Google Sign-In, multi-turn history, file attachments, starter prompt cards, model selector, and causal/web toggles. |
| **Gemini Enterprise Agent** | Built with the Agent Development Kit (ADK) and deployed to Vertex AI Agent Runtime, reachable over the reasoning-engine and A2A (Agent2Agent) contracts. |
| **Code Execution** | Gemini can write and run Python safely inside the Agent Sandbox to compute, model, and verify results. |
| **Google Sign-In & History** | Firebase Google auth; each signed-in user's conversations are persisted to Firestore and listed in the sidebar. |
| **File Attachments** | Upload text-extractable files (`.txt`, `.csv`, `.json`, code, …) to ground a prompt; contents ride as context blocks to the agent. |
| **Session Persistence** | Multi-turn context is retained by the Agent Engine's session service across a conversation. |
| **Lightweight Proxy** | A FastAPI proxy on Cloud Run hides Vertex credentials from the browser, using Application Default Credentials (no API keys in the client). |
| **Keyless CI/CD** | GitHub Actions deploys the agent, proxy, and hosting via Workload Identity Federation — zero long-lived keys. |
| **Firebase Hosting** | Static frontend served globally via CDN with automatic SSL and a custom domain (`tracerlensai.com`). |

---

## 🔄 How It Works

The app is split into two backends: a **proxy gateway** (`proxy/`, on Cloud Run) that the browser talks to, and the **ADK agent** (`src/`, on Vertex AI Agent Runtime) that does the reasoning.

```
┌─────────────┐   POST /analyze-prompt   ┌──────────────────────┐
│             │ ────────────────────────▶│ Firebase Hosting     │
│ User        │                          │ (CDN, custom domain) │
│ Browser     │ ◀──────────────────────── │ serves proxy/static  │
│             │   Markdown + Mermaid      └──────────┬───────────┘
└─────────────┘                                      │ rewrite / CORS direct
      ▲  Firebase Auth (Google Sign-In)              ▼
      │                                   ┌──────────────────────┐
      │                                   │ FastAPI Proxy        │
      │   Firestore ◀──── per-user ────── │ (Cloud Run)          │
      │   history        conversations    │ auth · upload · proxy│
      │                                   └──────────┬───────────┘
      │                                              │ reasoning-engine
      │                                              │ streamQuery (ADC)
      │                                              ▼
      │                                   ┌──────────────────────┐
      │                                   │ Vertex AI Agent Engine│
      │                                   │ (ADK Agent Runtime)   │
      │                                   │                       │
      │                                   │  Router → Causal      │
      │                                   │  Pipeline (web →      │
      │                                   │  decompose → DoWhy    │
      │                                   │  identify/estimate →  │
      │                                   │  execute → replan →   │
      │                                   │  synthesize) /        │
      │                                   │  General Assistant    │
      │                                   │  + sessions           │
      └───────────────────────────────── └──────────────────────┘
```

**Flow:** The browser sends a prompt (with an optional Firebase ID token and any uploaded attachment ids) to the proxy. The proxy authenticates the user, resolves attachments, prepends the `[[causal:on]]` marker when causal mode is on (and `[[web:on]]` when the Web toggle is on too), and streams the request to the Agent Engine with Application Default Credentials. The agent's **root router** dispatches to the **causal pipeline** (when marked) or the **general assistant**. In the pipeline, an optional web-search stage fetches data/evidence, the decomposer builds the graph, a deterministic DoWhy stage identifies (and, with data, estimates) the effect and corrects the DAG against the data, and the executor loop runs the plan. Pipeline progress rides on ADK event `state_delta`s, which the proxy collects into `causal_graph`, `causal_reasoning_steps`, `causal_status`, `causal_estimand`, `causal_effect`, `causal_counterfactual`, `causal_graph_reconcile`, and `causal_web_retrieval`. The proxy sums per-call token usage, persists the exchange to Firestore for signed-in users, and returns JSON. The frontend renders the Markdown answer, highlights code, draws the causal graph with its identification card, and updates the token counter.

---

## 📂 Repository Structure

For a detailed breakdown of every directory and file, see the [Repository Structure Guide](docs/repository_structure.md). At a glance:

```text
src/                # ADK agent (deployed to Vertex AI Agent Runtime)
  agent.py          #   root router + general assistant + engine wrapper
  fast_api_app.py   #   agent-side FastAPI server (adk_api, A2A, reasoning-engine)
  causal/           #   the causal-reasoning pipeline engine
  app_utils/        #   shared session/artifact services, A2A, telemetry
proxy/              # Cloud Run gateway (auth, uploads, history, agent proxy + static file serving)
  main.py           #   FastAPI proxy
ui/                 # React + Vite + TypeScript frontend (compiled to proxy/static via Docker)
  src/              #   App.tsx, components/, hooks/, lib/, styles.css
  package.json      #   Node dependencies
  vite.config.ts    #   Build config
terraform/          # GCP infrastructure as code
tests/              # pytest suite + Playwright UI tests + eval harness
Dockerfile.proxy    # Multi-stage: builds ui/ with Node then packages with proxy/
```

---

## 🚀 Getting Started

### Prerequisites

1. **Python 3.12** (and optionally **Docker Desktop** for the containerized dev flow).
2. **GCP Account** — a project with [Vertex AI APIs enabled](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com).
3. **Optional Tools** — `gcloud` CLI, `firebase` CLI, `terraform` (only for deploys / infra changes).

### Local Development

1. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env — set GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION, GOOGLE_CLOUD_LOCATION
   ```

2. **Authenticate for Vertex AI (ADC):**
   ```bash
   gcloud auth application-default login
   ```

3. **Install dependencies and run the tests:**
   ```bash
   pip install -r requirements.txt
   python -m pytest tests/ --ignore=tests/ui_tests -v
   ```

4. **Run the full stack locally with Docker Compose (recommended):**
   ```bash
   # Dockerfile.proxy builds the React UI (npm ci + vite build) and the proxy in one go.
   # AGENT_ENGINE_ENDPOINT unset → proxy returns a canned response + sample causal graph,
   # so the whole UI is developable offline.
   docker compose up --build
   # open http://localhost:8080
   ```

5. **Run the proxy only (no Docker, no GCP calls):**
   ```bash
   # Build the React app first:
   cd ui && npm ci && npm run build && cd ..
   uvicorn proxy.main:app --reload --port 8080
   # open http://localhost:8080
   ```

6. **Run the full stack locally (proxy + real ADK agent):** see
   [Local Development with Vertex AI Agent](docs/local_development_vertex_agent.md).

7. **Browser E2E tests (Playwright):**
   ```bash
   pip install -r requirements-dev.txt && playwright install chromium
   python -m pytest tests/ui_tests -v
   ```

> The `docker-compose.dev.yml` file also provides a hot-reload agent container
> (`tracerlensai-app`), a one-shot `test-runner`, and a Playwright `causal-agent-ui-test`
> service (see the [Developer Guide](docs/developer_guide.md#6-local-development-environment)).

---

## 🚀 Deployment & CI/CD

### Automated Deployment (Primary)

Authentication is fully keyless via **Workload Identity Federation** (OIDC). The [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) pipeline runs `deploy_to_gcp.sh` on every push to `main`, deploying all three tiers in order:

1. **Agent Engine** — `agents-cli deploy` packages `src/` and updates the Vertex AI Agent Runtime in place.
2. **Cloud Run proxy** — builds `Dockerfile.proxy` (multi-stage: Node 20 builds the React UI with `vite build`, then Python packages the proxy); deploys the `tracerlensai-proxy` service.
3. **Firebase Hosting** — publishes the `proxy/static/` output (pre-built React bundle) with the Cloud Run rewrite rule.

A manual `workflow_dispatch` can target a single stage (`agent`, `proxy`, or `hosting`).

### Manual Fallback Script

If CI/CD is unavailable, run the same script locally (needs `gcloud`, `agents-cli`, `docker`, and the `firebase` CLI):

```bash
./deploy_to_gcp.sh                # all three stages
./deploy_to_gcp.sh --only agent   # just the Agent Engine
./deploy_to_gcp.sh --only proxy   # just the Cloud Run proxy
./deploy_to_gcp.sh --only hosting # just Firebase Hosting
```

See the [Deployment Guide](docs/deployment_guide.md) and [Advanced Deployment](docs/advanced_deployment.md) for details.

---

## 📚 Documentation

| Document | Description |
|---|---|
| [Developer Guide](docs/developer_guide.md) | Architecture deep-dive, full API reference, and function-level docs for every source file |
| [Repository Structure](docs/repository_structure.md) | Directory-by-directory, file-by-file breakdown |
| [Causal Reasoning](docs/causal_reasoning.md) | How the causal pipeline retrieves data, decomposes, identifies/estimates (DoWhy + causal discovery), executes, and replans |
| [Evaluation & Testing](docs/evaluation_and_testing.md) | How both chat pathways are executed under test and scored — the `pytest` suite and the `agents-cli eval` flywheel |
| [Local Development (Vertex AI)](docs/local_development_vertex_agent.md) | Running the full stack locally against a real ADK agent |
| [Deployment Guide](docs/deployment_guide.md) | Step-by-step deployment methods and infrastructure provisioning |
| [Advanced Deployment](docs/advanced_deployment.md) | End-to-end multi-tier architecture, WIF, DNS, and rewrites |
| [Token Calculation](docs/token_calculation.md) | How token usage is measured and how multi-turn / multi-agent runs compound cost |
