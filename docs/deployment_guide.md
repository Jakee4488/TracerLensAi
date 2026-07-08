# Deployment Guide

This guide details every method available for deploying TracerLensAi, ordered by preference.

---

## Architecture Overview

TracerLensAi is deployed as a **two-tier** application:

| Tier | Service | What It Hosts |
|---|---|---|
| **Frontend** | Firebase Hosting | Static HTML/CSS/JS from `src/static/`, served via global CDN |
| **Backend** | Google Cloud Run | FastAPI application (Docker container), serverless and auto-scaling |

Firebase Hosting acts as the unified entry point. It serves static assets directly and proxies API calls to Cloud Run via rewrite rules in `firebase.json`.

---

## Method 1: Continuous Deployment via GitHub Actions (Primary)

The CI/CD pipeline is fully automated through GitHub Actions.

### CI — Pull Request Gate (`.github/workflows/ci.yml`)

**Trigger:** Every Pull Request targeting `main`.

**What it does:**
1. Checks out the code.
2. Creates a `.env` file from repository variables.
3. Runs the full `./run_tests.sh test` pipeline (lint, pytest, health checks).

PRs must pass CI before they can be merged.

### CD — Build & Deploy (`.github/workflows/cd.yml`)

**Trigger:** Push/merge to `main`, or manual dispatch from the GitHub Actions UI.

**What it does:**
1. Authenticates to GCP via **Workload Identity Federation** (OIDC) — no stored keys.
2. Builds the Docker image from the root `Dockerfile`.
3. Tags with both `git SHA` and `latest`.
4. Pushes to Google Container Registry (`gcr.io/icarus-agent-26/tracerlensai-app`).
5. Deploys to **Cloud Run** (default) or **GKE** (if `DEFAULT_TARGET=gke`).

**Required GitHub Repository Variables:**

| Variable | Example Value |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/NNNN/locations/global/workloadIdentityPools/github-actions-pool-v3/providers/github-actions-provider-v3` |
| `GCP_SERVICE_ACCOUNT` | `github-actions-sa@icarus-agent-26.iam.gserviceaccount.com` |
| `DEFAULT_TARGET` | `cloudrun` (or `gke`) |

**Manual Trigger:** Navigate to the GitHub Actions tab → "CD — Deploy to GCP" → "Run workflow" → Select `cloudrun` or `gke`.

---

## Method 2: Firebase Hosting Deployment (Frontend)

The static frontend is deployed separately from the backend.

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
3. Provisions (or renews) the SSL certificate for the custom domain.

---

## Method 3: Manual Fallback Script (Emergency)

If CI/CD is down or you need to deploy from your local machine.

**Prerequisites:**
- `gcloud` CLI installed and authenticated.
- A `.env` file at the project root with `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_REGION`.

**Command:**
```bash
# Deploy to Cloud Run (default)
./deploy_to_gcp.sh --target cloudrun

# Deploy to GKE (alternative)
./deploy_to_gcp.sh --target gke
```

**What it does:**
1. Sources `.env` for project configuration.
2. Configures Docker for GCR authentication.
3. Builds and pushes the Docker image.
4. Deploys to the specified target.

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
2. Runs `pytest` unit tests (with in-memory SQLite).
3. Runs health-check smoke tests against a spun-up container.
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
- **Service Accounts** (`agent-app-sa` for the app, `github-actions-sa` for CI/CD)
- **Workload Identity Federation** pool and provider for GitHub Actions
- **Artifact Registry** repositories for Docker images
- **GCS Buckets** for caching and MLOps artifacts
- **BigQuery Dataset** for agent orchestrator logs
- **API Enablement** for Artifact Registry, Cloud Functions, Cloud Run, and AI Platform

### 2. Firebase Hosting

```bash
firebase deploy --only hosting --project icarus-agent-26
```

### 3. Custom Domain (Optional)

1. Open the [Firebase Console](https://console.firebase.google.com/) → Hosting → Add Custom Domain.
2. Add the provided `A` and `TXT` records to your DNS provider (Cloud DNS, Google Domains, etc.).
3. Firebase automatically provisions a free SSL certificate once DNS propagates.

### 4. Helm / GKE (Optional)

If deploying to GKE instead of Cloud Run:
- Values like resources, replicas, and environment variables are in `helm/tracerlensai/values.yaml`.
- The `deploy_to_gcp.sh` script and `cd.yml` workflow handle `helm upgrade --install` automatically.
