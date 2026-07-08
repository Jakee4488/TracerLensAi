# Advanced Deployment Guide

This guide details the end-to-end, multi-tier deployment architecture for **TracerLensAi**. Our architecture leverages Google Cloud Platform (GCP) and Firebase to deliver a decoupled, highly scalable application with automated continuous delivery.

## Architecture Overview

The system is deployed as a decoupled application:
1. **Frontend (Static UI)**: Hosted globally on **Firebase Hosting**.
2. **Backend (FastAPI)**: Hosted dynamically on **Google Cloud Run**.
3. **API Routing (Gateway)**: Firebase Hosting acts as a reverse proxy, rewriting API requests directly to the Cloud Run backend.

---

## 1. Infrastructure Provisioning (Terraform)

All core infrastructure and IAM bindings are defined in the `terraform/` directory.

### Workload Identity Federation (WIF)
We completely eliminate the need for long-lived, insecure Service Account JSON keys by using Workload Identity Federation.
- **Provider**: `github-actions-provider-v3`
- **Pool**: `github-actions-pool-v3`
This allows our GitHub Actions runners to securely impersonate GCP Service Accounts during CI/CD.

### Cloud Run Service Account
We provision a dedicated service account (`agent-app-sa`) specifically for the Cloud Run instance. This service account is explicitly granted the `roles/aiplatform.user` role, which allows the backend to authenticate securely with Vertex AI and invoke LLMs (e.g., Gemini 2.5 Flash).

---

## 2. Backend Deployment (Cloud Run)

The backend is fully containerized and deployed via our automated CI/CD pipeline.

### GitHub Actions Pipeline (`.github/workflows/cd.yml`)
When changes are merged into the `main` branch, the Continuous Deployment (CD) pipeline triggers automatically:
1. **Authentication**: Uses Google's `auth` action with our WIF provider.
2. **Build**: Builds the Docker container containing the FastAPI application.
3. **Push**: Pushes the image to Google Artifact Registry.
4. **Deploy**: Deploys the new revision to Cloud Run (`tracerlensai-app`), explicitly binding it to our `agent-app-sa` service account to retain Vertex AI access.

---

## 3. Frontend Deployment (Firebase Hosting)

The frontend consists of static HTML, CSS, and JavaScript files located in the `src/static/` directory. We deploy these files to Firebase Hosting for global CDN caching and free SSL provisioning.

### Configuration (`firebase.json`)
Our `firebase.json` file is configured to map the root of our web app to the `src/static` directory:
```json
{
  "hosting": {
    "public": "src/static",
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
Because the frontend and backend are decoupled, the frontend JavaScript needs a way to securely call backend endpoints (like `/api/chats` or `/analyze-prompt`) without dealing with CORS issues or exposing the raw Cloud Run URL.

The `rewrites` block in `firebase.json` serves as our bridge:
- If a file exists in `src/static` (like `index.html` or `styles.css`), Firebase serves it instantly via CDN.
- If a request is made to a path that *doesn't* exist in the static folder (i.e., an API call), Firebase Hosting automatically acts as a reverse proxy, forwarding the request securely to the `tracerlensai-app` service on Cloud Run.

### Deployment Command
The frontend is deployed manually via the Firebase CLI (or can be added to CI/CD):
```bash
firebase deploy --only hosting
```
*(The `.firebaserc` file automatically targets our `icarus-agent-26` project).*

---

## 4. Custom Domain and DNS

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
