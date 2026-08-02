#!/bin/bash
# =============================================================================
# deploy_to_gcp.sh — One-step deployment of the TracerLensAi stack
#
# Deploys the full architecture in order:
#   1. agent   → Vertex AI Agent Engine (src/ via agents-cli, in-place update)
#   2. proxy   → Cloud Run service `tracerlensai-app` (proxy/ via Dockerfile.proxy)
#   3. hosting → Firebase Hosting (proxy/static, rewrites /* → Cloud Run)
#
# Usage:
#   ./deploy_to_gcp.sh                  # deploy all three stages
#   ./deploy_to_gcp.sh --only agent     # just the Agent Engine
#   ./deploy_to_gcp.sh --only proxy     # just the Cloud Run proxy
#   ./deploy_to_gcp.sh --only hosting   # just Firebase Hosting
#
# Requires: gcloud (authed), agents-cli, docker, firebase CLI or npx.
# Reads GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_REGION / AGENT_ENGINE_ENDPOINT
# from .env.
# =============================================================================
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

ONLY="all"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --only) ONLY="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

case "$ONLY" in
    all|agent|proxy|hosting) ;;
    *) echo "Invalid --only value: $ONLY (expected agent, proxy, or hosting)"; exit 1 ;;
esac

if [ -f ".env" ]; then
    source .env
else
    echo "Error: .env file missing. Needed for GOOGLE_CLOUD_PROJECT."
    exit 1
fi

PROJECT_ID=${GOOGLE_CLOUD_PROJECT}
REGION=${GOOGLE_CLOUD_REGION:-europe-west2}
SERVICE_NAME="tracerlensai-app"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

# ── Stage 1: Agent Engine ────────────────────────────────────────────────────
# Source-based (deployment_source) update of the engine recorded in
# deployment_metadata.json. The engine cannot be updated with the SDK's
# pickle-based package_spec flow — agents-cli is the only supported updater.
if [ "$ONLY" = "all" ] || [ "$ONLY" = "agent" ]; then
    echo "▶ [1/3] Deploying ADK agent (src/) to Vertex AI Agent Engine..."
    command -v agents-cli >/dev/null || { echo "agents-cli not found. Install with: pip install uv google-agents-cli"; exit 1; }
    command -v uv >/dev/null || { echo "uv not found (agents-cli needs it for packaging). Install with: pip install uv"; exit 1; }
    agents-cli deploy \
        --project "${PROJECT_ID}" \
        --region "${REGION}" \
        --no-confirm-project
fi

# ── Stage 2: Cloud Run proxy ─────────────────────────────────────────────────
if [ "$ONLY" = "all" ] || [ "$ONLY" = "proxy" ]; then
    echo "▶ [2/3] Deploying proxy (proxy/) to Cloud Run service ${SERVICE_NAME}..."
    gcloud auth configure-docker gcr.io --quiet
    docker build -f Dockerfile.proxy -t "${IMAGE_NAME}" .
    docker push "${IMAGE_NAME}"

    DEPLOY_ARGS=(
        --image "${IMAGE_NAME}"
        --region "${REGION}"
        --project "${PROJECT_ID}"
        --allow-unauthenticated
    )
    # Point the proxy at the Agent Engine. deployment_metadata.json is kept
    # current by agents-cli on every agent deploy, so it is the source of
    # truth; an explicit AGENT_ENGINE_ENDPOINT env var overrides it.
    if [ -z "${AGENT_ENGINE_ENDPOINT:-}" ] && [ -f deployment_metadata.json ]; then
        ENGINE_ID=$(sed -n 's/.*"remote_agent_runtime_id": *"\([^"]*\)".*/\1/p' deployment_metadata.json)
        if [ -n "${ENGINE_ID}" ]; then
            AGENT_ENGINE_ENDPOINT="https://${REGION}-aiplatform.googleapis.com/v1beta1/${ENGINE_ID}:query"
        fi
    fi
    if [ -n "${AGENT_ENGINE_ENDPOINT:-}" ]; then
        DEPLOY_ARGS+=(--update-env-vars "AGENT_ENGINE_ENDPOINT=${AGENT_ENGINE_ENDPOINT}")
    fi
    # Cross-origin allow-list, so the app (Firebase Hosting) can call the
    # Cloud Run service directly (e.g. via api.tracerlensai.com) and bypass
    # Hosting's 60s timeout on long causal runs. Empty = same-origin only.
    # Requires a separate `gcloud run domain-mappings create` + DNS record for
    # the api subdomain, and TRACERLENS_API_BASE set in the frontend.
    CORS_ORIGINS="${CORS_ORIGINS:-https://tracerlensai.com,https://api.tracerlensai.com}"
    if [ -n "${CORS_ORIGINS}" ]; then
        # ^##^ sets '##' as the delimiter so the commas inside the value are
        # not parsed by gcloud as separate env-var assignments.
        DEPLOY_ARGS+=(--update-env-vars "^##^CORS_ORIGINS=${CORS_ORIGINS}")
    fi
    gcloud run deploy "${SERVICE_NAME}" "${DEPLOY_ARGS[@]}"
fi

# ── Stage 3: Firebase Hosting ────────────────────────────────────────────────
# Builds the React UI, then publishes ui/dist and the rewrite rule
# (firebase.json) that routes /analyze-prompt and every other non-static path
# to the Cloud Run proxy.
if [ "$ONLY" = "all" ] || [ "$ONLY" = "hosting" ]; then
    echo "▶ [3/3] Building UI and deploying Firebase Hosting (ui/dist + rewrites)..."
    (cd ui && npm ci && npm run build)
    if command -v firebase >/dev/null; then
        firebase deploy --only hosting --project "${PROJECT_ID}" --non-interactive
    else
        npx -y firebase-tools deploy --only hosting --project "${PROJECT_ID}" --non-interactive
    fi
fi

echo "✅ Deployment finished. Live at https://tracerlensai.com"
