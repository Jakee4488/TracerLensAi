# Repository Structure

This document provides a detailed breakdown of every directory and file in the TracerLensAi codebase.

For a high-level overview of the project, see the [README](../README.md).

---

## Top-Level Layout

```text
TracerLensAi/
├── src/                             # Core Application
├── terraform/                       # GCP Infrastructure as Code
├── .github/                         # CI/CD Pipelines & Badges
├── tests/                           # Test Suite
├── helm/                            # Optional GKE Helm Charts
├── docs/                            # Documentation
├── Dockerfile                       # Container build definition
├── docker-compose.dev.yml           # Local development environment
├── requirements.txt                 # Python dependencies
├── run_tests.sh                     # Dev/test automation script
├── deploy_to_gcp.sh                 # Manual deployment fallback
├── firebase.json                    # Firebase Hosting configuration
├── .firebaserc                      # Firebase project binding
└── .env.example                     # Environment variable template
```

---

## `src/` — Core Application

The entire application lives here. There are no sub-packages — the backend is intentionally kept flat for simplicity.

```text
src/
├── main.py                          # FastAPI backend
├── database.py                      # SQLite persistence layer
└── static/                          # Frontend assets
    ├── index.html                   # Main UI shell
    ├── causal-agent.js              # Client-side chat logic
    └── styles.css                   # Design system (dark/light mode)
```

| File | Role |
|---|---|
| `main.py` | The single FastAPI entrypoint. Defines all API endpoints (`/health`, `/api/chats`, `/analyze-prompt`), the GenAI client factory, Pydantic request models, and the Gemini integration logic. |
| `database.py` | A lightweight SQLite persistence layer. Manages two tables: `chats` (sessions with token counts) and `messages` (user/AI messages with optional causal reasoning steps). |
| `static/index.html` | The HTML shell with a two-panel layout: a collapsible sidebar (navigation + history) and the main chat area (header controls, messages, input). |
| `static/causal-agent.js` | All client-side logic: sending messages, creating chat sessions, loading history, rendering AI responses as Markdown with syntax highlighting, and managing UI state (dark mode, token badge). |
| `static/styles.css` | The full CSS design system using custom properties. Defines dark and light mode palettes, responsive breakpoints (sidebar collapses at ≤768px), animations (typing indicator), and all component styles. |

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
│   ├── cd.yml                       # Continuous deployment to Cloud Run
│   └── uptime.yml                   # Health check & uptime badge
└── badges/
    └── uptime.json                  # shields.io badge data
```

| Workflow | Trigger | Action |
|---|---|---|
| `ci.yml` | Pull request → `main` | Runs `./run_tests.sh test` (flake8 + pytest) |
| `cd.yml` | Push to `main` or manual dispatch | Build Docker → Push to GCR → Deploy to Cloud Run (or GKE) |
| `uptime.yml` | Every 5 minutes (cron) | Pings `/health`, updates `uptime.json` badge |

---

## `tests/` — Test Suite

```text
tests/
├── conftest.py                      # Pytest fixtures (test client, temp DB)
├── test_main.py                     # API endpoint tests
├── test_database.py                 # Database function tests
└── ui_tests/
    └── test_ui.py                   # Playwright browser tests
```

Tests use a monkeypatched SQLite database in a temporary directory for full isolation.

---

## `helm/` — Optional GKE Deployment

```text
helm/tracerlensai/
├── Chart.yaml                       # Helm chart metadata
├── values.yaml                      # Configurable values (image, replicas, env)
└── templates/                       # Kubernetes manifests
```

Only used if deploying to GKE instead of Cloud Run. The `cd.yml` workflow and `deploy_to_gcp.sh` script both support `--target gke`.

---

## Root Configuration Files

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build: Stage 1 installs Python deps, Stage 2 creates a non-root runtime with graphviz. Runs `uvicorn` on port 8080. |
| `docker-compose.dev.yml` | Three services: `tracerlensai-app` (hot-reload dev), `test-runner` (pytest), `causal-agent-ui-test` (Playwright). |
| `requirements.txt` | FastAPI, uvicorn, pydantic, google-genai, pytest, flake8, playwright. |
| `run_tests.sh` | Docker-based automation: `test`, `--start`, `--stop`, `--clean`, `--commit`. |
| `deploy_to_gcp.sh` | Manual fallback: builds, pushes, deploys to Cloud Run or GKE. |
| `firebase.json` | Firebase Hosting config: serves `src/static/`, rewrites API calls to Cloud Run. |
| `.firebaserc` | Binds Firebase CLI to project `icarus-agent-26`. |
| `.env.example` | Template for `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_REGION`, `GEMINI_API_KEY`. |
| `pytest.ini` | Pytest configuration. |
| `.flake8` | Flake8 linting rules. |
| `.gitignore` | Ignores venvs, terraform state, credentials, Firebase cache, DB files. |
