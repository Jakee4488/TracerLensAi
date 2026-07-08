# Deployment Guide

This guide details every method available for deploying TracerLensAi, ordered by preference.

---

## Architecture Overview

TracerLensAi is deployed as a **three-tier** application leveraging the Gemini Enterprise Agent Platform:

| Tier | Service | What It Hosts |
|---|---|---|
| **Frontend** | Firebase Hosting | Static HTML/CSS/JS from `src/static/`, served via global CDN |
| **Proxy Backend**| Google Cloud Run | Lightweight FastAPI application acting as a secure reverse proxy |
| **Agent Runtime**| Vertex AI Agent Engine | ADK Agent (`src/agent.py`) utilizing the native Memory Bank and tools |

Firebase Hosting acts as the unified entry point. It serves static assets directly and proxies API calls to the Cloud Run proxy via rewrite rules. The proxy securely forwards prompts to the Agent Runtime, hiding API keys from the browser.

---

## Method 1: Continuous Deployment via GitHub Actions (Primary)

The CI/CD pipeline is fully automated through GitHub Actions (`.github/workflows/cd.yml`).

**Trigger:** Push/merge to `main`, or manual dispatch from the GitHub Actions UI.

**What it does:**
1. Authenticates to GCP via **Workload Identity Federation** (OIDC) — no stored keys.
2. Evaluates the agent using `agents-cli eval`.
3. Packages and pushes the ADK Agent to the Vertex AI Agent Registry.
4. Builds the FastAPI proxy Docker image.
5. Pushes to Google Artifact Registry.
6. Deploys the proxy to **Cloud Run**.

**Required GitHub Repository Variables:**

| Variable | Example Value |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/NNNN/locations/global/workloadIdentityPools/...` |
| `GCP_SERVICE_ACCOUNT` | `github-actions-sa@icarus-agent-26.iam.gserviceaccount.com` |

---

## Method 2: Firebase Hosting Deployment (Frontend)

The static frontend is deployed separately from the backend and agent.

**Prerequisites:**
- Firebase CLI installed (`npm install -g firebase-tools`)
- Authenticated (`firebase login`)

**Command:**
```bash
firebase deploy --only hosting --project icarus-agent-26
```

**What it does:**
1. Uploads all files in `src/static/` to Firebase Hosting CDN.
2. Applies the rewrite rules from `firebase.json`, which proxy API requests to Cloud Run.

---

## Method 3: Manual Fallback Script (Emergency)

If CI/CD is down or you need to deploy from your local machine.

**Prerequisites:**
- `gcloud` CLI installed and authenticated.
- `google-agents-cli` installed (`pip install google-agents-cli`).
- A `.env` file at the project root with `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_REGION`.

**Command:**
```bash
# Deploy the ADK Agent to the Agent Runtime
./deploy_to_gcp.sh --target agent-runtime

# Deploy the Proxy to Cloud Run
./deploy_to_gcp.sh --target cloudrun
```

> [!WARNING]
> Manual deployment should only be used as a last resort. Always prefer standard branch merges which trigger the automated CD pipeline.

---

## Method 4: Local Dev & Commit Guard

For safe local development, use `run_tests.sh` to ensure only passing code gets committed.

**Command:**
```bash
./run_tests.sh --commit "Your descriptive commit message"
```

**What it does:**
1. Runs `flake8` linting.
2. Runs `pytest` unit tests (using the `TestClient` for isolated endpoint testing).
3. Runs health-check smoke tests against a spun-up local proxy container.
4. If all pass, displays the `git diff` and commits the code.
5. Prompts you to push to remote, which then triggers the CD pipeline (Method 1).

---

## Infrastructure Provisioning (First-Time Setup)

If this is a brand-new environment, the infrastructure must be provisioned before the application can deploy.

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
- **Service Accounts** (`agent-app-sa` for the proxy, `github-actions-sa` for CI/CD)
- **Workload Identity Federation** pool and provider for GitHub Actions
- **Artifact Registry** repositories for Docker images
- **GCS Buckets** for caching and Agent artifacts
- **BigQuery Dataset** for orchestrator logs
- **API Enablement** for Vertex AI, Cloud Run, and Artifact Registry.

### 2. Firebase Hosting

```bash
firebase deploy --only hosting --project icarus-agent-26
```

### 3. Custom Domain (Optional)

1. Open the [Firebase Console](https://console.firebase.google.com/) → Hosting → Add Custom Domain.
2. Add the provided `A` and `TXT` records to your DNS provider (Cloud DNS, Google Domains, etc.).
3. Firebase automatically provisions a free SSL certificate once DNS propagates.
