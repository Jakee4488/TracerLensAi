# TracerLensAi

🌐 **[Live Application](https://tracerlensai.com/)** · ![Build](https://github.com/Jakee4488/TracerLensAi/actions/workflows/cd.yml/badge.svg) · ![Uptime](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Jakee4488/TracerLensAi/main/.github/badges/uptime.json)

**TracerLensAi** is a cloud-native AI chat interface powered by Google Gemini. It provides an interactive workspace for evaluating agentic workflows with causal reasoning, live code execution, and web search — all backed by a production-grade GCP deployment pipeline.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Gemini Integration** | Direct access to `gemini-2.5-flash` and `gemini-2.5-pro` via the `google-genai` SDK with Vertex AI backend |
| **Code Execution** | Gemini can write and run Python code inline using the native Code Execution tool |
| **Web Search** | Toggle Google Search grounding to give Gemini real-time internet access |
| **Causal Reasoning** | Optional second-pass analysis that identifies confounders, proposes structural causal models, and estimates treatment effects |
| **Multi-Model Selection** | Switch between Gemini models from the UI header dropdown |
| **Chat Persistence** | Full conversation history stored in SQLite with per-chat token tracking |
| **Dark / Light Mode** | iOS-style toggle for dark and light themes |
| **Keyless CI/CD** | GitHub Actions deploys to Cloud Run via Workload Identity Federation — zero long-lived keys |
| **Firebase Hosting** | Static frontend served globally via CDN with automatic SSL and custom domain (`tracerlensai.com`) |

---

## 🔄 How It Works
<img width="1912" height="946" alt="image" src="https://github.com/user-attachments/assets/0b80699b-f328-4912-a4e5-906889c5df87" />


```
┌─────────────┐      POST /analyze-prompt      ┌──────────────────────┐
│             │ ──────────────────────────────▶│                      │
│ User        │                                │ Firebase             │
│ Browser     │ ◀───────────────────────────── │ Hosting (CDN)        │
│             │    Rendered Markdown + Code    │                      │
└─────────────┘                                └───────────┬──────────┘
                                                           │ Rewrite Proxy
                                                           ▼
                                               ┌──────────────────────┐
                                               │                      │
                                               │ FastAPI              │
                                               │ (Cloud Run)          │
                                               │                      │
                                               └───┬──────────────┬───┘
                                                   │              │
                                          Load     │              │ generate_content()
                                          History  │              │
                                                   ▼              ▼
                                              ┌──────────┐   ┌──────────┐
                                              │ SQLite   │   │ Gemini   │
                                              │ (Chat    │   │ (Vertex  │
                                              │ Store)   │   │ AI)      │
                                              └──────────┘   └──────────┘
```

**Flow:** User sends a prompt → Firebase Hosting serves the static UI and proxies the API call to Cloud Run → FastAPI loads chat history from SQLite, calls Gemini with full context → Gemini returns a response with token usage → FastAPI persists the message and returns JSON → The frontend parses Markdown, highlights code blocks, and updates the token counter.

---

## 📂 Repository Structure

For a detailed breakdown of every directory and file, see the [Repository Structure Guide](docs/repository_structure.md).

---

## 🚀 Getting Started

### Prerequisites

1. **Docker & Docker Compose** — Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).

2. **GCP Account** — A project with [Vertex AI APIs enabled](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com).
3. **Optional Tools** — `gcloud` CLI, `terraform` (only for infrastructure changes).

### Local Development

1. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env — set GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION, and optionally GEMINI_API_KEY
   ```


2. **Run the full test pipeline:**
   ```bash
   chmod +x run_tests.sh
   ./run_tests.sh test
   ```
   This builds the Docker image, runs `flake8` linting, `pytest` unit tests, and health-check smoke tests.


3. **Start the hot-reload dev server:**
   ```bash
   ./run_tests.sh --start
   ```
   The app will be live at `http://localhost:8080` with automatic reload on file changes.

4. **Stop the dev server:**
   ```bash
   ./run_tests.sh --stop
   ```


5. **Clean all Docker resources:**
   ```bash
   ./run_tests.sh --clean
   ```


---

## 🚀 Deployment & CI/CD

### Automated Deployment (Primary)

| Trigger | Workflow | Action |
|---|---|---|
| Pull Request → `main` | `ci.yml` | Lint + test gate (must pass before merge) |
| Push / Merge → `main` | `cd.yml` | Build → Push to GCR → Deploy to Cloud Run |
| Manual dispatch | `cd.yml` | Choose `cloudrun` or `gke` target from GitHub Actions UI |

Authentication is fully keyless via **Workload Identity Federation** (OIDC). No service account JSON keys are stored anywhere.

### Firebase Hosting (Frontend)

The static frontend is deployed separately to Firebase Hosting:
```bash
firebase deploy --only hosting --project icarus-agent-26
```

Firebase Hosting serves static files globally via CDN and proxies API requests to Cloud Run via the rewrite rules in `firebase.json`.

### Manual Fallback Script

If CI/CD is unavailable:
```bash
# Deploy to Cloud Run
./deploy_to_gcp.sh --target cloudrun

# Deploy to GKE (alternative)
./deploy_to_gcp.sh --target gke
```

> [!WARNING]
> Manual deployment should only be used as a last resort. Always prefer standard branch merges.

---

## 📚 Documentation

| Document | Description |
|---|---|
| [Developer Guide](docs/developer_guide.md) | Architecture deep-dive, full API reference, function-level docs for every source file |
| [Repository Structure](docs/repository_structure.md) | Detailed breakdown of every directory and file in the codebase |
| [Deployment Guide](docs/deployment_guide.md) | Step-by-step deployment methods and infrastructure provisioning |
| [Advanced Deployment](docs/advanced_deployment.md) | Terraform, WIF, Firebase Hosting, and Cloud Run architecture details |
