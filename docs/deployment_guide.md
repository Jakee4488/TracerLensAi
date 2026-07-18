# Deployment Guide

This guide details every method available for deploying TracerLensAi, ordered by preference.

---

## Architecture Overview

TracerLensAi is deployed as a **three-tier** application on the Gemini Enterprise Agent Platform:

| Tier | Service | What it hosts |
|---|---|---|
| **Frontend** | Firebase Hosting | Static HTML/CSS/JS from `proxy/static/`, served via global CDN |
| **Proxy Gateway** | Google Cloud Run | Lightweight FastAPI proxy (`proxy/main.py`): auth, history, uploads, agent proxy |
| **ADK Agent** | Vertex AI Agent Runtime | The ADK agent (`src/`) with session persistence and tools |

Firebase Hosting is the unified entry point: it serves static assets directly and rewrites all other paths to the Cloud Run proxy. The proxy authenticates users (Firebase), persists history (Firestore), and securely forwards prompts to the Agent Runtime with Application Default Credentials — **no API keys reach the browser**. For long causal runs, the frontend can call Cloud Run directly (e.g. `api.tracerlensai.com`) to bypass Hosting's 60s timeout (see CORS below).

All three tiers deploy from one script, [`deploy_to_gcp.sh`](../deploy_to_gcp.sh).

---

## Method 1: Continuous Deployment via GitHub Actions (Primary)

The pipeline is fully automated through [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml).

**Trigger:** push/merge to `main`, or manual dispatch (with an optional single stage).

**What it does:**
1. Records a GitHub Deployment against the `production` environment (linked to `https://tracerlensai.com`).
2. Authenticates to GCP via **Workload Identity Federation** (OIDC) — no stored keys.
3. Installs `uv` + `google-agents-cli`.
4. Writes a minimal `.env` (project + region) and runs `deploy_to_gcp.sh`, which deploys **Agent Engine → Cloud Run proxy → Firebase Hosting**.
5. Updates the GitHub Deployment status (success/failure surfaced in run annotations).

A manual `workflow_dispatch` accepts a `stage` input (`all`, `agent`, `proxy`, or `hosting`) to redeploy just one tier.

**Required GitHub repository variables:**

| Variable | Example value |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/NNNN/locations/global/workloadIdentityPools/github-actions-pool-v3/providers/github-actions-provider-v3` |
| `GCP_SERVICE_ACCOUNT` | `github-actions-sa@icarus-agent-26.iam.gserviceaccount.com` |

> `AGENT_ENGINE_ENDPOINT` is intentionally **not** a repo variable: the script
> derives it from `deployment_metadata.json`, which `agents-cli` keeps current
> on every agent deploy. (A stale repo-level var previously broke the proxy.)

---

## Method 2: Manual Fallback Script (Emergency)

If CI/CD is down or you need to deploy from your machine. This is the exact script CI runs.

**Prerequisites:**
- `gcloud` CLI installed and authenticated (`gcloud auth login` + `gcloud auth application-default login`).
- `uv` and `google-agents-cli` (`pip install uv google-agents-cli`).
- `docker` and the `firebase` CLI (or `npx firebase-tools`).
- A `.env` at the project root with at least `GOOGLE_CLOUD_PROJECT` (and optionally `GOOGLE_CLOUD_REGION`).

**Commands:**
```bash
./deploy_to_gcp.sh                  # all three stages, in order
./deploy_to_gcp.sh --only agent     # just the Agent Engine (agents-cli)
./deploy_to_gcp.sh --only proxy     # just the Cloud Run proxy (Dockerfile.proxy)
./deploy_to_gcp.sh --only hosting   # just Firebase Hosting (proxy/static + rewrites)
```

> [!WARNING]
> Prefer standard branch merges, which trigger the automated CD pipeline.
> Use the manual script only as a fallback.

---

## Method 3: Firebase Hosting Only (Frontend)

The static frontend can be redeployed on its own:

```bash
firebase deploy --only hosting --project icarus-agent-26
# or:
./deploy_to_gcp.sh --only hosting
```

This uploads `proxy/static/` and applies the `rewrites` from `firebase.json`, which proxy all non-static requests to the `tracerlensai-app` Cloud Run service.

---

## Method 4: Local Verification Before Pushing

There is no `run_tests.sh` — verify locally with pytest and (optionally) the mock proxy:

```bash
# Unit + integration tests (proxy + causal engine)
pip install -r requirements.txt
python -m pytest tests/ --ignore=tests/ui_tests -v

# Optional: browser E2E against the mock-mode proxy
pip install -r requirements-dev.txt && playwright install chromium
python -m pytest tests/ui_tests -v

# Optional: eyeball the UI against the mock agent (no GCP calls)
uvicorn proxy.main:app --reload --port 8080
```

Once green, merge to `main` to trigger Method 1.

---

## Cloud Run Environment Variables

The proxy service (`tracerlensai-app`) reads:

| Variable | Set by | Purpose |
|---|---|---|
| `AGENT_ENGINE_ENDPOINT` | `deploy_to_gcp.sh` (from `deployment_metadata.json`) | The Agent Engine `:query` URL to proxy to. Unset → mock mode. |
| `CORS_ORIGINS` | `deploy_to_gcp.sh` (default `https://tracerlensai.com,https://api.tracerlensai.com`) | Cross-origin allow-list for direct Cloud Run calls. |
| `FIRESTORE_DATABASE_ID` | optional | Named Firestore database (default `tracerlensai`). |
| `MAX_UPLOAD_BYTES` | optional | Upload size cap (default 5 MB). |

Auth to Vertex uses the service account's Application Default Credentials — there is **no** API key to configure.

---

## Infrastructure Provisioning (First-Time Setup)

For a brand-new environment, provision infrastructure before the first app deploy.

### 1. Terraform

```bash
cd terraform/
gcloud auth application-default login
terraform init
terraform plan    # Review what will be created
terraform apply   # Provision resources
```

This provisions:
- **Cloud Run service** (`tracerlensai-app`) with public access
- **Service accounts** (`agent-app-sa` for the proxy, `github-actions-sa` for CI/CD)
- **Workload Identity Federation** pool (`github-actions-pool-v3`) and provider for GitHub Actions
- **Artifact Registry** repositories for Docker images
- **GCS buckets** for caching and causal artifacts
- **BigQuery dataset** for orchestrator logs
- **API enablement** for Vertex AI, Cloud Run, Cloud Functions, and Artifact Registry

### 2. Firebase Hosting

```bash
firebase deploy --only hosting --project icarus-agent-26
```

### 3. Custom Domain (Optional)

1. Firebase Console → Hosting → Add Custom Domain (`tracerlensai.com`).
2. Add the provided `A` and `TXT` records to your DNS provider (Cloud DNS).
3. Firebase provisions a free auto-renewing SSL certificate once DNS propagates.
4. (Optional) For the `api.` subdomain used to bypass Hosting's 60s cap, create a Cloud Run domain mapping and DNS record, then set `window.TRACERLENS_API_BASE` in `index.html`.
