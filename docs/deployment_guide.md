# Deployment Guide

Every way TracerLensAi gets deployed, ordered by preference, plus the
infrastructure and environment it needs. This absorbs what used to be a separate
"Advanced Deployment" document.

---

## Architecture Overview

TracerLensAi deploys as a **three-tier** application:

| Tier | Service | What it hosts |
|---|---|---|
| **Frontend** | Firebase Hosting | The compiled React bundle from `ui/dist`, served via global CDN |
| **Proxy Gateway** | Google Cloud Run | FastAPI gateway (`proxy/`): access gate, history, uploads, agent proxy, `/admin` |
| **ADK Agent** | Vertex AI Agent Runtime | The ADK agent (`src/`) — router, general assistant, causal pipeline |

Firebase Hosting is the entry point: it serves static assets directly and
rewrites every other path to the Cloud Run proxy. The proxy gates access by
email ([access_control.md](access_control.md)), persists history in Firestore,
and forwards prompts to the Agent Runtime using Application Default
Credentials — **no API key ever reaches the browser**.

All three tiers deploy from one script, [`deploy_to_gcp.sh`](../deploy_to_gcp.sh).

> **Bypassing the 60s cap.** Firebase Hosting rewrites time out at 60 seconds,
> which a long causal run exceeds. The frontend can call Cloud Run directly via
> the `api.` subdomain instead; `ui/index.html` selects that base URL for the
> production hostnames, and the proxy's `CORS_ORIGINS` allow-list permits it.

### Environments

Three GitHub Environments map to three Cloud Run services:

| Workflow | Trigger | Cloud Run service | Stages run |
|---|---|---|---|
| [`deploy.yml`](../.github/workflows/deploy.yml) | push to `main`, or manual dispatch | `tracerlensai-app` | agent → proxy → hosting |
| [`deploy-dev.yml`](../.github/workflows/deploy-dev.yml) | push to any branch except `main` | `tracerlensai-app-dev` (shared) | proxy only |
| [`deploy-staging.yml`](../.github/workflows/deploy-staging.yml) | pull request | `tracerlensai-app-staging-pr-<N>` | proxy only |

> Dev and staging deliberately run **`--only proxy`**. `firebase.json` carries a
> single rewrite target, so a preview deploy must never touch hosting. The
> practical consequence: **changes under `src/` are not exercised by any preview
> deploy** — a preview always talks to whichever Agent Engine revision is
> currently live. Test agent changes locally
> ([local_development_vertex_agent.md](local_development_vertex_agent.md)) or on `main`.

---

## Method 1: Continuous Deployment via GitHub Actions (Primary)

**Trigger:** push/merge to `main`, or manual dispatch with an optional single stage.

**What it does:**

1. Records a GitHub Deployment against the `production` environment.
2. Authenticates to GCP via **Workload Identity Federation** (OIDC) — no stored keys.
3. Installs `uv` + `google-agents-cli`.
4. Writes a minimal `.env` and runs `deploy_to_gcp.sh`, deploying
   **Agent Engine → Cloud Run proxy → Firebase Hosting**.
5. Updates the GitHub Deployment status.

A manual `workflow_dispatch` accepts a `stage` input (`all`, `agent`, `proxy`,
`hosting`) to redeploy one tier.

### Required repository **variables**

| Variable | Example |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/NNNN/locations/global/workloadIdentityPools/github-actions-pool-v3/providers/github-actions-provider-v3` |
| `GCP_SERVICE_ACCOUNT` | `github-actions-sa@icarus-agent-26.iam.gserviceaccount.com` |

### Required per-environment **variables**

| Variable | Why |
|---|---|
| `APP_URL` | Public origin for this environment. **Load-bearing** — the proxy refuses to boot without it (it signs login links). |
| `ACCESS_NOTIFY_EMAIL` | Where access requests are emailed. |

### Required **secrets**

| Secret | Consequence if missing |
|---|---|
| `ACCESS_SIGNING_SECRET` | Every cold start invalidates all sessions — everyone is signed out continuously. |
| `ADMIN_TOKEN` | `/admin` answers 503. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | No outbound mail; access links can't be sent. `RESEND_API_KEY` is the alternative transport (SMTP takes precedence). |

> `AGENT_ENGINE_ENDPOINT` is intentionally **not** a repo variable: the script
> derives it from `deployment_metadata.json`, which `agents-cli` keeps current on
> every agent deploy. A stale repo-level value previously broke the proxy.

---

## Method 2: Manual Fallback Script (Emergency)

The exact script CI runs.

**Prerequisites:** `gcloud` (authenticated, plus `gcloud auth application-default
login`), `uv` and `google-agents-cli`, `docker`, `node` 20+, and the `firebase`
CLI (or `npx firebase-tools`). A root `.env` with at least `GOOGLE_CLOUD_PROJECT`.

```bash
./deploy_to_gcp.sh                  # all three stages, in order
./deploy_to_gcp.sh --only agent     # Agent Engine (agents-cli)
./deploy_to_gcp.sh --only proxy     # Cloud Run proxy (Dockerfile.proxy)
./deploy_to_gcp.sh --only hosting   # builds ui/ then publishes ui/dist
```

> [!WARNING]
> Prefer branch merges, which trigger Method 1. Use the script only as a fallback.
>
> Access-gate variables are forwarded **only when set in your shell**, so a
> partial deploy never blanks a value already on the service. The flip side: if
> `ACCESS_SIGNING_SECRET` is unset locally the script warns and leaves whatever
> the service has.

---

## Method 3: Firebase Hosting Only (Frontend)

```bash
./deploy_to_gcp.sh --only hosting
```

This runs `npm ci && npm run build` in `ui/`, then publishes `ui/dist` and the
`rewrites` from `firebase.json`, which route every non-static path to the
`tracerlensai-app` Cloud Run service.

> Do **not** run a bare `firebase deploy --only hosting` unless you have just
> built the UI — Firebase publishes whatever is in `ui/dist`, which may be stale
> or absent.

---

## Method 4: Local Verification Before Pushing

```bash
# Unit + integration tests (proxy + causal engine)
pip install -r requirements.txt
python -m pytest tests/ --ignore=tests/ui_tests -v

# Browser E2E. Needs a built bundle first — the proxy serves ui/dist, not source.
cd ui && npm ci && npm run build && cd ..
pip install -r requirements-dev.txt && playwright install chromium
python -m pytest tests/ui_tests -v

# Eyeball the UI against the mock agent (no GCP calls, no spend)
MODE=mock docker compose up --build      # http://localhost:8080
```

CI runs the first block plus `uv lock --check` and the UI's
`lint`/`typecheck`/`build`. It does **not** run the Playwright suite.

---

## Cloud Run Environment Variables

`deploy_to_gcp.sh` forwards these to the proxy service when they are set in the
deploying environment.

### Boot-critical

| Variable | Purpose |
|---|---|
| `APP_URL` | Public origin. **The revision refuses to start without it.** |
| `ACCESS_SIGNING_SECRET` | HMAC key for session and login-link tokens. Unset ⇒ every cold start signs everyone out. |
| `ADMIN_TOKEN` | Admin dashboard password. Unset ⇒ `/admin` returns 503. |

### Routing and limits

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_ENGINE_ENDPOINT` | from `deployment_metadata.json` | Agent Engine `:query` URL. Unset ⇒ mock mode. |
| `CORS_ORIGINS` | `https://tracerlensai.com,https://api.tracerlensai.com` | Allow-list for direct Cloud Run calls. |
| `FIRESTORE_DATABASE_ID` | `tracerlensai` | Named database. Set it for staging, or staging shares production's records. |
| `MAX_UPLOAD_BYTES` | 5 MB | Upload size cap. |
| `UPLOAD_DIR` | unset | Directory for upload sidecars; in-memory only when unset. |
| `UI_DIST` | `ui/dist` | Where the compiled bundle is served from. |

### Access gate and quota

| Variable | Default | Purpose |
|---|---|---|
| `ACCESS_TOKEN_LIMIT` | 200000 | Per-user token quota. |
| `ACCESS_TOKEN_GRANT` | 200000 | Tokens added when an extension is approved. |
| `CHAT_RETENTION_HOURS` | 24 | How long conversations survive. Backs the privacy promise. |
| `RUN_METRICS_RETENTION_DAYS` | 30 | How long `agent_runs` telemetry rows survive. |
| `ACCESS_STORE` | unset | `memory` swaps Firestore for a process-local dict. **Dev only.** |
| `ALLOW_LOCALHOST_APP_URL` | unset | Permits a localhost `APP_URL`, which is otherwise rejected. |

### Mail

`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, or `RESEND_API_KEY` as
the alternative transport (SMTP wins when both are set), plus
`ACCESS_NOTIFY_EMAIL` and `ACCESS_FROM_EMAIL`.

Auth to Vertex uses the service account's Application Default Credentials —
there is **no** API key to configure. There are, however, several secrets above.

---

## Infrastructure Provisioning (First-Time Setup)

### 1. Terraform

```bash
cd terraform/
gcloud auth application-default login
terraform init
terraform plan    # review
terraform apply
```

Provisions:
- **Cloud Run service** (`tracerlensai-app`) with public access
- **Service accounts** — `agent-app-sa` (proxy runtime), `github-actions-sa` (CI/CD)
- **Workload Identity Federation** pool `github-actions-pool-v3` and its provider,
  which is what removes long-lived service-account JSON keys from CI entirely
- **Artifact Registry** repositories for Docker images
- **GCS buckets** for caching and causal artifacts
- **BigQuery dataset** for orchestrator logs
- **API enablement** for Vertex AI, Cloud Run, Cloud Functions, Artifact Registry

> [!NOTE]
> `terraform/iam.tf` grants `agent-app-sa` the `aiplatform.user`,
> `bigquery.dataEditor`, and `logging.logWriter` roles. `deploy_to_gcp.sh` also
> expects it to hold a Firestore role (`datastore.user`) for the access gate and
> history, and **Terraform does not grant that** — bind it out of band on a new
> project.

### 2. Firebase Hosting

```bash
./deploy_to_gcp.sh --only hosting
```

### 3. Custom Domain (Optional)

1. Firebase Console → Hosting → Add Custom Domain (`tracerlensai.com`).
2. Add the provided `A` and `TXT` records to Cloud DNS.
3. Firebase provisions a free auto-renewing SSL certificate once DNS propagates.
4. For the `api.` subdomain that bypasses the 60s cap, create a Cloud Run domain
   mapping and DNS record. `ui/index.html` already selects that base for the
   production hostnames.

---

## See Also

- [Developer Guide](developer_guide.md) — architecture, API reference, SSE contract
- [Access Control](access_control.md) — the email gate, quota, and admin dashboard
- [Local Development (Vertex AI)](local_development_vertex_agent.md) — running against a real agent
