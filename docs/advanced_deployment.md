# Advanced Deployment Guide

This guide details the end-to-end, multi-tier deployment architecture for **TracerLensAi**. Our architecture leverages Google Cloud Platform (GCP), Firebase, and the **Gemini Enterprise Agent Platform** to deliver a decoupled, highly scalable application with automated continuous delivery.

## Architecture Overview

The system is deployed as a decoupled three-tier application:
1. **Frontend (Static UI)**: Hosted globally on **Firebase Hosting**.
2. **Backend Proxy (FastAPI)**: Hosted dynamically on **Google Cloud Run**.
3. **Agent Runtime (Vertex AI)**: Hosts the core ADK agent logic (`src/agent.py`) and manages state natively via the **Memory Bank**.

Firebase Hosting acts as a reverse proxy, rewriting API requests directly to the Cloud Run backend proxy. The proxy authenticates the request with Application Default Credentials (no stored API keys) and forwards it to the Vertex AI Agent Engine. It also handles Firebase Google Sign-In, per-user history in Firestore, and file uploads.

---

## 1. Infrastructure Provisioning (Terraform)

All core infrastructure and IAM bindings are defined in the `terraform/` directory.

### Workload Identity Federation (WIF)
We completely eliminate the need for long-lived, insecure Service Account JSON keys by using Workload Identity Federation.
- **Provider**: `github-actions-provider-v3`
- **Pool**: `github-actions-pool-v3`
This allows our GitHub Actions runners to securely impersonate GCP Service Accounts during CI/CD.

### Cloud Run Service Account
We provision a dedicated service account (`agent-app-sa`) specifically for the Cloud Run instance. This service account is explicitly granted the `roles/aiplatform.user` role, which allows the proxy backend to securely authenticate with Vertex AI and invoke the deployed ADK Agent.

---

## 2. Agent Deployment (Vertex AI Agent Engine)

The core logic of TracerLensAi now resides in `src/agent.py`, authored using the Agent Development Kit (ADK).

### Deployment Process
The `.github/workflows/deploy.yml` workflow utilizes `google-agents-cli` (via `deploy_to_gcp.sh`) to package and deploy the agent:
1. **Validation**: `agents-cli eval` validates the agent syntax and tests it.
2. **Deployment**: `agents-cli deploy` packages the agent and uploads it to the Vertex AI Agent Registry.
3. **Execution**: Once deployed, the Agent Engine hosts the agent and automatically manages the **Memory Bank** (conversational history) and MCP Tools (Code Execution, Web Search).

---

## 3. Backend Proxy Deployment (Cloud Run)

The proxy backend (`proxy/main.py`) is fully containerized (`Dockerfile.proxy`) and deployed via our automated CI/CD pipeline.

### GitHub Actions Pipeline
In the same `.github/workflows/deploy.yml` pipeline:
1. **Build**: Builds the Docker container containing the lightweight FastAPI proxy.
2. **Push**: Pushes the image to Google Artifact Registry.
3. **Deploy**: Deploys the new revision to Cloud Run (`tracerlensai-app`), explicitly binding it to our `agent-app-sa` service account.

---

## 4. Frontend Deployment (Firebase Hosting)

The frontend consists of static HTML, CSS, and JavaScript files located in the `proxy/static/` directory. We deploy these files to Firebase Hosting for global CDN caching and free SSL provisioning.

### Configuration (`firebase.json`)
Our `firebase.json` file is configured to map the root of our web app to the `proxy/static` directory:
```json
{
  "hosting": {
    "public": "proxy/static",
    "ignore": [
      "firebase.json",
      "**/.*",
      "**/node_modules/**"
    ],
    "rewrites": [
      {
        "source": "**",
        "run": {
          "serviceId": "tracerlensai-app",
          "region": "europe-west2"
        }
      }
    ]
  }
}
```

### Routing and Rewrites (The Bridge)
Because the frontend and backend are decoupled, the frontend JavaScript needs a way to securely call backend endpoints (like `/analyze-prompt`) without dealing with CORS issues or exposing the raw Cloud Run URL.

The `rewrites` block in `firebase.json` serves as our bridge:
- If a file exists in `proxy/static` (like `index.html` or `styles.css`), Firebase serves it instantly via CDN.
- If a request is made to an API path, Firebase Hosting automatically acts as a reverse proxy, forwarding the request securely to the `tracerlensai-app` service on Cloud Run.

> **Bypassing the 60s cap.** Firebase Hosting rewrites time out at 60s. For long
> causal runs the frontend can instead call Cloud Run directly via a dedicated
> `api.` subdomain (`window.TRACERLENS_API_BASE`), enabled by the proxy's
> `CORS_ORIGINS` allow-list.

### Deployment Command
The frontend is deployed manually via the Firebase CLI (or can be added to CI/CD):
```bash
firebase deploy --only hosting
```

---

## 5. Custom Domain and DNS

We use Firebase Hosting to manage our custom domain (**tracerlensai.com**).
1. The domain is registered via Google Cloud Domains.
2. DNS is managed via **Cloud DNS**.
3. In the Firebase Console, we added the custom domain, which provided specific `A` and `TXT` Resource Records.
4. We added these Resource Records to the Cloud DNS zone.
5. Firebase automatically monitors the DNS propagation and provisions a free, auto-renewing Let's Encrypt SSL certificate for HTTPS secure traffic.

---

## See Also

- [Developer Guide](developer_guide.md) — Full architecture, API reference, and function-level documentation
- [Deployment Guide](deployment_guide.md) — Step-by-step deployment methods
- [Token Calculation](token_calculation.md) — Mathematical model for token compounding in multi-turn workflows
