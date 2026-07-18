# TracerLensAi

🌐 **[Live Application](https://tracerlensai.com/)** · ![Build](https://github.com/Jakee4488/TracerLensAi/actions/workflows/deploy.yml/badge.svg)

**TracerLensAi** is a cloud-native AI chat interface built on the **Gemini Enterprise Agent Platform**. It pairs a general-purpose Gemini assistant with a deterministic **causal-reasoning pipeline** that decomposes a problem into a causal graph, plans and executes it step-by-step, propagates the impact of failures through the graph, and replans only the affected subgraph — all rendered live in the UI as a Mermaid diagram. The stack is a production-grade GCP deployment: an ADK agent on Vertex AI Agent Runtime, a lightweight FastAPI proxy on Cloud Run, Firebase Auth + Firestore for per-user history, and Firebase Hosting on a custom domain.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Causal Reasoning Pipeline** | A multi-agent ADK pipeline (decompose → plan → execute → replan → synthesize) with deterministic graph engineering in Python. See [Causal Reasoning](docs/causal_reasoning.md). |
| **Live Causal Graph** | The reasoning graph (nodes, causal edges, critical path, per-step status) streams to the browser and renders as a Mermaid diagram with a status legend. |
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
      │                                   │  Pipeline / General   │
      │                                   │  Assistant + sessions │
      └───────────────────────────────── └──────────────────────┘
```

**Flow:** The browser sends a prompt (with an optional Firebase ID token and any uploaded attachment ids) to the proxy. The proxy authenticates the user, resolves attachments, prepends the `[[causal:on]]` marker when causal mode is on, and streams the request to the Agent Engine with Application Default Credentials. The agent's **root router** dispatches to the **causal pipeline** (when marked) or the **general assistant**. Pipeline progress rides on ADK event `state_delta`s, which the proxy collects into `causal_graph`, `causal_reasoning_steps`, and `causal_status`. The proxy sums per-call token usage, persists the exchange to Firestore for signed-in users, and returns JSON. The frontend renders the Markdown answer, highlights code, draws the causal graph, and updates the token counter.

---

## 📂 Repository Structure

For a detailed breakdown of every directory and file, see the [Repository Structure Guide](docs/repository_structure.md). At a glance:

```text
src/                # ADK agent (deployed to Vertex AI Agent Runtime)
  agent.py          #   root router + general assistant + engine wrapper
  fast_api_app.py   #   agent-side FastAPI server (adk_api, A2A, reasoning-engine)
  causal/           #   the causal-reasoning pipeline engine
  app_utils/        #   shared session/artifact services, A2A, telemetry
proxy/              # Cloud Run gateway (auth, uploads, history, agent proxy)
  main.py           #   FastAPI proxy
  static/           #   vanilla HTML/CSS/JS frontend
terraform/          # GCP infrastructure as code
tests/              # pytest suite + Playwright UI tests + eval harness
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

4. **Run the proxy against the mock agent (no GCP calls):**
   ```bash
   # AGENT_ENGINE_ENDPOINT unset → the proxy returns a canned response and a
   # sample causal graph, so the whole UI is developable offline.
   uvicorn proxy.main:app --reload --port 8080
   # open http://localhost:8080
   ```

5. **Run the full stack locally (proxy + real ADK agent):** see
   [Local Development with Vertex AI Agent](docs/local_development_vertex_agent.md).

6. **Browser E2E tests (Playwright):**
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
2. **Cloud Run proxy** — builds `Dockerfile.proxy` and deploys the `tracerlensai-app` service.
3. **Firebase Hosting** — publishes `proxy/static/` with the Cloud Run rewrite rule.

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
| [Causal Reasoning](docs/causal_reasoning.md) | How the causal pipeline decomposes, plans, executes, and replans |
| [Local Development (Vertex AI)](docs/local_development_vertex_agent.md) | Running the full stack locally against a real ADK agent |
| [Deployment Guide](docs/deployment_guide.md) | Step-by-step deployment methods and infrastructure provisioning |
| [Advanced Deployment](docs/advanced_deployment.md) | End-to-end multi-tier architecture, WIF, DNS, and rewrites |
| [Token Calculation](docs/token_calculation.md) | How token usage is measured and how multi-turn / multi-agent runs compound cost |
| [Migration & Testing Guide](docs/migration_testing_guide.md) | Verifying and shipping the Agent Platform migration |
