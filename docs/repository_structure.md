# Repository Structure

This document provides a detailed breakdown of every directory and file in the TracerLensAi codebase.

For a high-level overview of the project, see the [README](../README.md).

---

## Top-Level Layout

```text
TracerLensAi/
├── .github/                         # CI/CD Pipelines
├── docs/                            # Documentation
├── proxy/                           # FastAPI proxy & static frontend
├── src/                             # Core Agent logic (ADK)
├── terraform/                       # GCP Infrastructure as Code
├── test-agent/                      # Secondary agent scaffold project
├── tests/                           # Test Suite
├── .env.example                     # Environment variable template
├── agents-cli-manifest.yaml         # Config for Gemini Enterprise Agent Platform
├── deploy_to_gcp.sh                 # One-step deployment: Agent Engine → Cloud Run → Firebase Hosting
├── docker-compose.dev.yml           # Local development environment
├── Dockerfile                       # Container build definition for Agent
├── Dockerfile.proxy                 # Container build definition for Proxy
├── firebase.json                    # Firebase Hosting configuration
└── requirements.txt                 # Python dependencies
```

---

## `src/` & `proxy/` — Core Application & Gateway

The application is split into the core agent logic (`src/`) and a lightweight proxy server (`proxy/`).

```text
src/
├── agent.py                         # ADK Agent Logic
├── fast_api_app.py                  # FastAPI implementation for the Agent
└── app_utils/                       # Helper utilities (telemetry, typing, etc.)

proxy/
├── main.py                          # FastAPI backend proxy serving as a gateway
└── static/                          # Frontend assets
    ├── index.html                   # Main UI shell
    ├── causal-agent.js              # Client-side chat logic
    └── styles.css                   # Design system
```

| File | Role |
|---|---|
| `proxy/main.py` | The FastAPI entrypoint that acts as a lightweight proxy, forwarding `/analyze-prompt` requests to the Vertex AI Agent Engine. |
| `src/agent.py` | Implements the Agent Development Kit (ADK) logic, registers tools, and relies on the platform Memory Bank for persistence. |
| `proxy/static/index.html` | The HTML shell with a two-panel layout: a collapsible sidebar and the main chat area. |
| `proxy/static/causal-agent.js` | Client-side logic for chat sessions, rendering Markdown, and managing UI state. |
| `proxy/static/styles.css` | CSS design system for dark/light mode, animations, and component styles. |

---

## `terraform/` — Infrastructure as Code

All GCP resources are declared here using Terraform (hashicorp/google provider ~> 5.0).

```text
terraform/
├── main.tf                          # Provider configuration
├── variables.tf                     # Input variables
├── cloudrun.tf                      # Cloud Run service + public IAM
├── iam.tf                           # Service accounts & Workload Identity Federation
├── storage.tf                       # Artifact Registry, GCS, BigQuery
├── causal_mlops.tf                  # API enablement + MLOps resources
└── outputs.tf                       # Terraform outputs
```

| File | Resources Managed |
|---|---|
| `main.tf` | Google provider configuration, required Terraform version (≥1.5.0) |
| `variables.tf` | `project_id` (icarus-agent-26), `region` (europe-west2), `github_repo`, `causal_artifact_repo_name`, `causal_artifacts_bucket` |
| `cloudrun.tf` | `google_cloud_run_service` (tracerlensai-app) with `agent-app-sa` service account, public access via `allUsers` invoker IAM |
| `iam.tf` | `agent-app-sa` (roles: aiplatform.user, bigquery.dataEditor, logging.logWriter), `github-actions-sa` (owner), WIF pool `github-actions-pool-v3` + provider, GKE Workload Identity binding, Artifact Registry reader for compute SA |
| `storage.tf` | Artifact Registry (`agent-docker-repo`), GCS bucket (`agent-cache`), BigQuery dataset (`agent_orchestrator_logs`, 365-day TTL) |
| `causal_mlops.tf` | API enablement (Artifact Registry, Cloud Functions, Cloud Run, AI Platform), Causal MLOps Artifact Registry (`causal-mlops-repo`), GCS bucket for causal artifacts |
| `outputs.tf` | `artifact_registry_repo` name |

---

## `.github/` — CI/CD & Automation

```text
.github/
├── workflows/
│   ├── ci.yml                       # PR gate — lint & test
│   ├── deploy.yml                   # Continuous deployment
│   └── uptime.yml                   # Health check & uptime badge
└── badges/
    └── uptime.json                  # shields.io badge data
```

| Workflow | Trigger | Action |
|---|---|---|
| `ci.yml` | Pull request → `gemini-agent-platform` | Runs the backend pytest suite |
| `deploy.yml` | Push to `gemini-agent-platform` or manual dispatch | One-step pipeline via `deploy_to_gcp.sh`: Agent Engine → Cloud Run proxy → Firebase Hosting (dispatch can target a single stage) |
| `uptime.yml` | Every 5 minutes (cron) | Pings `/health`, updates `uptime.json` badge |

---

## `tests/` — Test Suite

```text
tests/
├── conftest.py                      # Pytest fixtures (test client)
├── test_main.py                     # API endpoint tests
└── ui_tests/
    └── test_ui.py                   # Playwright browser tests
```

Tests use the `TestClient` for isolated endpoint testing.

---

## Root Configuration Files

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build definition for the core application. |
| `Dockerfile.proxy` | Multi-stage build definition specifically for the proxy server. |
| `docker-compose.dev.yml` | Services: `tracerlensai-app` (hot-reload dev proxy), `test-runner` (pytest), `causal-agent-ui-test` (Playwright). |
| `requirements.txt` | FastAPI, uvicorn, pydantic, google-genai, google-adk, agents-cli, pytest, flake8. |
| `agents-cli-manifest.yaml` | Configuration for deploying via the Gemini Enterprise Agent Platform. |
| `deploy_to_gcp.sh` | One-step deployment: Agent Engine (agents-cli) → Cloud Run proxy (Dockerfile.proxy) → Firebase Hosting; `--only agent\|proxy\|hosting` deploys a single stage. |
| `firebase.json` | Firebase Hosting config: serves `proxy/static/`, rewrites API calls to Cloud Run. |
| `.firebaserc` | Binds Firebase CLI to project `icarus-agent-26`. |
| `.env.example` | Template for `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_REGION`, `GEMINI_API_KEY`. |
| `pytest.ini` | Pytest configuration. |
| `.flake8` | Flake8 linting rules. |
| `.gitignore` | Ignores venvs, terraform state, credentials, Firebase cache, DB files. |
