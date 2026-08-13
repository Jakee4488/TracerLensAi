# TracerLensAi

🌐 **[Live Application](https://tracerlensai.com/)** · ![Build](https://github.com/Jakee4488/TracerLensAi/actions/workflows/deploy.yml/badge.svg)

**TracerLensAi** is a cloud-native AI chat interface built on the **Gemini Enterprise Agent Platform**. It pairs a general-purpose Gemini assistant with a deterministic **causal-reasoning pipeline** that decomposes a problem into a causal graph, formally identifies any treatment effect with DoWhy (correcting the graph against real data via causal discovery when a dataset is present), plans and executes it step-by-step, propagates the impact of failures through the graph, and replans only the affected subgraph — all rendered live in the UI as an interactive ReactFlow DAG. It can pull observational data or evidence from the web to ground that analysis. The stack is a production-grade GCP deployment: an ADK agent on Vertex AI Agent Runtime, a lightweight FastAPI proxy on Cloud Run, an email-gated access system with per-user token quotas, Firestore for history, and Firebase Hosting on a custom domain.

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
| **React + Vite Frontend** | The UI is a production-grade React 18 + TypeScript + Vite application (`ui/`) compiled to a static bundle and served by the FastAPI proxy. Dark/light theme toggle, email sign-in, multi-turn history, file attachments, starter prompt cards, model selector, and causal/web toggles. |
| **Gemini Enterprise Agent** | Built with the Agent Development Kit (ADK) and deployed to Vertex AI Agent Runtime, reachable over the reasoning-engine and A2A (Agent2Agent) contracts. |
| **Code Execution** | Gemini can write and run Python safely inside the Agent Sandbox to compute, model, and verify results. |
| **Email Access Gate** | The agent is closed by default: visitors request access with an email address, you approve from a one-click link, and they sign in via an emailed link. Each approved address carries a 200K-token quota, extendable on request. See [docs/access_control.md](docs/access_control.md). |
| **Sign-In & History** | Passwordless email sign-in; a signed-in user's conversations are persisted to Firestore, listed in the sidebar, and deleted automatically after 24 hours. |
| **Admin Dashboard** | `/admin`, behind a password plus a one-time code emailed to the owner. Reviews pending access and extension requests, and shows per-user token usage, failure rates, and latency. |
| **File Attachments** | Upload text-extractable files (`.txt`, `.csv`, `.json`, code, …) to ground a prompt; contents ride as context blocks to the agent. |
| **Session Persistence** | Multi-turn context is retained by the Agent Engine's session service across a conversation. |
| **Lightweight Proxy** | A FastAPI proxy on Cloud Run hides Vertex credentials from the browser, using Application Default Credentials (no API keys in the client). |
| **Keyless CI/CD** | GitHub Actions deploys the agent, proxy, and hosting via Workload Identity Federation — zero long-lived keys. |
| **Firebase Hosting** | Static frontend served globally via CDN with automatic SSL and a custom domain (`tracerlensai.com`). |

---

## 🔄 How It Works

The app is split into two backends: a **proxy gateway** (`proxy/`, on Cloud Run) that the browser talks to, and the **ADK agent** (`src/`, on Vertex AI Agent Runtime) that does the reasoning.

```text
┌─────────────┐    static assets (CDN)   ┌──────────────────────┐
│             │ ◀────────────────────────│ Firebase Hosting     │
│ User        │                          │ (CDN, custom domain) │
│ Browser     │                          │ publishes ui/dist    │
│             │                          └──────────┬───────────┘
└─────────────┘                                     │ rewrite
      │  ▲                                          │ (or CORS direct
      │  │  Server-Sent Events                      │  to skip the 60s cap)
      │  │  progress · graph · done                 ▼
      │  │                                ┌──────────────────────┐
      │  └────────────────────────────────│ FastAPI Proxy        │
      └───────────────────────────────────▶ (Cloud Run)          │
         POST /analyze-prompt             │ access gate · quota  │
                                          │ history · uploads    │
         Firestore ◀──── per-user ────────│                      │
         history         conversations    └──────────┬───────────┘
                                                     │ reasoning-engine
                                                     │ streamQuery (ADC)
                                                     ▼
                                          ┌────────────────────────┐
                                          │ Vertex AI Agent Engine │
                                          │ (ADK Agent Runtime)    │
                                          │                        │
                                          │  Router → Causal       │
                                          │  Pipeline (web →       │
                                          │  decompose → DoWhy     │
                                          │  identify/estimate →   │
                                          │  execute → replan →    │
                                          │  synthesize) /         │
                                          │  General Assistant     │
                                          │  + sessions            │
                                          └────────────────────────┘
```

**Flow:** The browser sends a prompt (with its access-session token and any uploaded attachment ids) to the proxy. The proxy checks the access gate and the caller's token quota, resolves attachments, prepends the `[[causal:on]]` marker when causal mode is on (plus `[[web:on]]` for the Web toggle and `[[run:<id>]]` as a correlation id), and streams the request to the Agent Engine with Application Default Credentials. The agent's **root router** dispatches to the **causal pipeline** (when marked) or the **general assistant**. In the pipeline, an optional web-search stage fetches data/evidence, the decomposer builds the graph, a deterministic DoWhy stage identifies (and, with data, estimates) the effect and corrects the DAG against the data, and the executor loop runs the plan.

Pipeline progress rides on ADK event `state_delta`s. The proxy turns those into **Server-Sent Events** — `progress`, `graph`, and a final `done` frame carrying the full report — so the graph and timeline fill in live rather than appearing at the end. The proxy sums per-call token usage, charges it against the caller's quota, and persists the exchange to Firestore. The frontend renders the Markdown answer, highlights code, draws the causal DAG with its identification card, and updates the token counter.

---

## 📂 Repository Structure

For a detailed breakdown of every directory and file, see the [Repository Structure Guide](docs/repository_structure.md). At a glance:

```text
src/                # ADK agent (deployed to Vertex AI Agent Runtime)
  agent.py          #   root router + general assistant + engine wrapper
  fast_api_app.py   #   agent-side FastAPI server (adk_api, A2A, reasoning-engine)
  causal/           #   the causal-reasoning pipeline engine
  app_utils/        #   shared session/artifact services, A2A, telemetry
proxy/              # Cloud Run gateway
  main.py           #   FastAPI gateway: SSE chat, history, uploads, static serving
  access.py         #   email access gate, sessions, token quota, mail
  admin.py          #   /admin dashboard behind password + emailed OTP
  memstore.py       #   in-memory Firestore stand-in for offline dev
ui/                 # React + Vite + TypeScript frontend (compiled to ui/dist)
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

1. **Python 3.12** and **Node 20+** (the UI must be compiled — the proxy serves `ui/dist`, not source).
2. **Docker Desktop**, for the containerized dev flow.
3. **GCP Account** — a project with [Vertex AI APIs enabled](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com). Not needed for the offline flow below.
4. **Optional Tools** — `gcloud` CLI, `firebase` CLI, `terraform` (only for deploys / infra changes).

### Local Development

1. **Clone and configure:**

   ```bash
   git clone https://github.com/Jakee4488/TracerLensAi.git
   cd TracerLensAi
   cp .env.example .env
   # Edit .env — set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_REGION (europe-west2)
   ```

2. **Install dependencies and run the tests:**

   ```bash
   pip install -r requirements.txt
   python -m pytest tests/ --ignore=tests/ui_tests -v
   ```

3. **Run the full stack offline (no GCP, no spend):**

   ```bash
   MODE=mock docker compose up --build
   # open http://localhost:8080
   ```

   `Dockerfile.proxy` builds the React UI and the proxy in one go. `MODE=mock`
   returns a canned response plus a sample causal graph, so the whole UI is
   developable offline.

   > [!WARNING]
   > `MODE` defaults to **`real`**. A bare `docker compose up` calls the live
   > Vertex AI Agent Engine and **costs money**. Pass `MODE=mock` explicitly.
   >
   > The compose files also bind-mount `${APPDATA}` for Application Default
   > Credentials and are Windows-first. On macOS/Linux, replace that line in
   > `docker-compose.yml` with `~/.config/gcloud`.

4. **Run the proxy only (no Docker):**

   ```bash
   cd ui && npm ci && npm run build && cd ..
   ACCESS_STORE=memory ADMIN_TOKEN=local-admin APP_URL=http://localhost:8080 \
     uvicorn proxy.main:app --reload --port 8080
   ```

   All three variables are required: without `ACCESS_STORE=memory` the access
   gate wants real Firestore credentials, without `APP_URL` the app refuses to
   boot, and without `ADMIN_TOKEN` `/admin` returns 503.

5. **Run against a real ADK agent:** see
   [Local Development with Vertex AI Agent](docs/local_development_vertex_agent.md).

6. **Browser E2E tests (Playwright):**

   ```bash
   cd ui && npm ci && npm run build && cd ..   # required — the suite serves ui/dist
   pip install -r requirements-proxy.txt -r requirements-dev.txt
   playwright install chromium
   python -m pytest tests/ui_tests -v
   ```

   `requirements.txt` covers the agent, not the proxy this suite boots — hence
   `requirements-proxy.txt`. CI runs this suite on every PR
   ([`ci.yml`](.github/workflows/ci.yml)); it needs no credentials, because
   `ACCESS_MAIL_TRANSPORT=console` prints sign-in links to the server log.

> `docker-compose.dev.yml` provides a hot-reload agent container
> (`tracerlensai-app`, port 8080), the `proxy` on port 8081, a one-shot
> `test-runner`, and a Playwright `causal-agent-ui-test` service. See the
> [Developer Guide](docs/developer_guide.md#8-local-development).

---

## 🚀 Deployment & CI/CD

### Automated Deployment (Primary)

Authentication is fully keyless via **Workload Identity Federation** (OIDC). The [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) pipeline runs `deploy_to_gcp.sh` on every push to `main`, deploying all three tiers in order:

1. **Agent Engine** — `agents-cli deploy` packages `src/` and updates the Vertex AI Agent Runtime in place.
2. **Cloud Run proxy** — builds `Dockerfile.proxy` (multi-stage: Node 20 builds the React UI with `vite build`, then Python packages the proxy); deploys the `tracerlensai-app` service.
3. **Firebase Hosting** — builds `ui/` and publishes `ui/dist` with the Cloud Run rewrite rule.

A manual `workflow_dispatch` can target a single stage (`agent`, `proxy`, or `hosting`).

### Preview environments

| Workflow | Trigger | Service | Stages |
|---|---|---|---|
| [`deploy.yml`](.github/workflows/deploy.yml) | push to `main` | `tracerlensai-app` | agent → proxy → hosting |
| [`deploy-dev.yml`](.github/workflows/deploy-dev.yml) | push to any other branch | `tracerlensai-app-dev` | proxy only |
| [`deploy-staging.yml`](.github/workflows/deploy-staging.yml) | pull request | `tracerlensai-app-staging-pr-<N>` | proxy only |

Dev and staging run **`--only proxy`** — `firebase.json` has a single rewrite
target, so a preview must never touch hosting. The consequence worth knowing:
**changes under `src/` are not exercised by any preview deploy**; a preview
always talks to whichever Agent Engine revision is currently live.

### Manual Fallback Script

If CI/CD is unavailable, run the same script locally (needs `gcloud`, `agents-cli`, `docker`, and the `firebase` CLI):

```bash
./deploy_to_gcp.sh                # all three stages
./deploy_to_gcp.sh --only agent   # just the Agent Engine
./deploy_to_gcp.sh --only proxy   # just the Cloud Run proxy
./deploy_to_gcp.sh --only hosting # just Firebase Hosting
```

See the [Deployment Guide](docs/deployment_guide.md) for environments, secrets, WIF, and DNS.

---

## 📚 Documentation

Start at the [documentation index](docs/README.md), or jump straight in:

| Document | Description |
|---|---|
| [Developer Guide](docs/developer_guide.md) | Architecture, API reference, the SSE contract between the two backends, local setup |
| [Repository Structure](docs/repository_structure.md) | Directory-by-directory, file-by-file breakdown |
| [Causal Reasoning](docs/causal_reasoning.md) | How the pipeline retrieves data, decomposes, identifies/estimates (DoWhy + causal discovery), executes, and replans |
| [Access Control](docs/access_control.md) | The email gate, token quota, 24-hour retention, and the admin dashboard |
| [Evaluation & Testing](docs/evaluation_and_testing.md) | The `pytest` suite and the `agents-cli eval` flywheel |
| [Local Development (Vertex AI)](docs/local_development_vertex_agent.md) | Running the full stack locally against a real ADK agent |
| [Deployment Guide](docs/deployment_guide.md) | Deployment methods, environments, secrets, and infrastructure provisioning |
| [Token Calculation](docs/token_calculation.md) | How token usage is measured and how multi-agent runs compound cost |

---

## 🤝 Contributing

1. Branch from `main`. Pushing to any other branch deploys it to the shared dev URL.
2. Before opening a PR, run what CI runs:

   ```bash
   uv lock --check
   python -m pytest tests/ --ignore=tests/ui_tests -v
   cd ui && npm run lint && npm run typecheck && npm run build && cd ..
   python -m pytest tests/ui_tests -v   # Playwright E2E — see Getting Started §6
   ```

3. Two invariants are easy to break and are enforced by tests:
   - **Vertex tool isolation** — every `LlmAgent` carries at most one of
     `{code_executor, output_schema, tools}`. Vertex rejects built-in tools mixed
     with function declarations.
   - **The two backends stay separate** — `proxy/` and `src/` share only the
     message markers, the `causal_` state-key prefix, and the agent names in
     `STAGE_BY_AUTHOR`.

---

## 📄 License

Licensed under the **[GNU Affero General Public License v3.0](LICENSE)**.

AGPL-3.0 is a strong copyleft licence with a network clause: if you run a
modified version of this software as a **hosted or network-accessible service**,
you must offer the complete corresponding source of your modified version to its
users. That obligation applies to running it over a network, not only to
distributing binaries — which is the reason this licence was chosen for a project
that is primarily deployed as a web application.

Copyright (C) 2026 Jakee4488.
