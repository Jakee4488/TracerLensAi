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
#   # A preview copy on its own Cloud Run URL, leaving production alone.
#   # Pair it with --only proxy: the hosting stage publishes the live domain,
#   # and the agent stage updates the one shared engine in place.
#   DEPLOY_SERVICE_NAME=tracerlensai-app-staging APP_URL=https://... \
#     ./deploy_to_gcp.sh --only proxy
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
# DEPLOY_SERVICE_NAME retargets the whole proxy stage at another Cloud Run
# service, which is how the staging workflow gets a full copy of the app on its
# own URL without touching the live one. Deliberately not named SERVICE_NAME or
# IMAGE_NAME: .env already carries an IMAGE_NAME for the GKE path, and this file
# sources .env, so reusing either name would let a local .env silently retarget
# a production deploy.
SERVICE_NAME="${DEPLOY_SERVICE_NAME:-tracerlensai-app}"
# Tagged per service, so a staging build can never be promoted to production by
# a `latest` tag that both services happen to share.
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
        # Explicit, not left to default: `gcloud run deploy` only preserves an
        # existing service's service account on a REDEPLOY. The very first
        # deploy of any new SERVICE_NAME (every dev push, every PR preview)
        # falls back to the project's default Compute Engine SA, which has
        # none of agent-app-sa's grants (datastore.user, aiplatform.user) —
        # every Firestore write then fails with a 403 that has nothing to do
        # with the code path that triggered it. Reusing the same runtime SA
        # across all three tiers is safe: its Firestore role is project-scoped
        # (applies to every named database, not just "tracerlensai"), and
        # per-tier isolation already comes from FIRESTORE_DATABASE_ID plus a
        # separate Cloud Run service, not from a separate identity.
        --service-account "agent-app-sa@${PROJECT_ID}.iam.gserviceaccount.com"
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

    # ── Access gate (docs/access_control.md) ─────────────────────────────────
    # Only forwarded when present in the environment, so a partial deploy never
    # blanks a value already set on the service. The two secrets are the ones
    # that matter: without ACCESS_SIGNING_SECRET every cold start signs all
    # users out, and without ADMIN_TOKEN the dashboard answers 503.
    # FIRESTORE_DATABASE_ID is here so a staging service can be pointed at its
    # own database; unset, proxy/access.py defaults to "tracerlensai" and
    # staging shares production's records. SMTP_* mirrors RESEND_API_KEY as the
    # alternative mail transport.
    for VAR in ACCESS_SIGNING_SECRET ADMIN_TOKEN RESEND_API_KEY \
               SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD \
               ACCESS_NOTIFY_EMAIL ACCESS_FROM_EMAIL APP_URL \
               FIRESTORE_DATABASE_ID \
               ACCESS_TOKEN_LIMIT ACCESS_TOKEN_GRANT \
               CHAT_RETENTION_HOURS RUN_METRICS_RETENTION_DAYS; do
        if [ -n "${!VAR:-}" ]; then
            DEPLOY_ARGS+=(--update-env-vars "${VAR}=${!VAR}")
        fi
    done
    if [ -z "${ACCESS_SIGNING_SECRET:-}" ]; then
        echo "WARNING: ACCESS_SIGNING_SECRET is not set in this environment." >&2
        echo "         Leaving whatever the service already has. If it has none," >&2
        echo "         every cold start invalidates all sessions." >&2
    fi
    if [ -z "${APP_URL:-}" ]; then
        echo "WARNING: APP_URL is not set in this environment." >&2
        echo "         Leaving whatever the service already has. If it has none," >&2
        echo "         the revision now refuses to start rather than mail out" >&2
        echo "         sign-in links pointing at http://localhost:8080." >&2
        echo "         Set it to the public origin, e.g.:" >&2
        echo "           export APP_URL=https://tracerlensai.com" >&2
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

# Report what was actually deployed. A dev or preview run targets a different
# Cloud Run service via DEPLOY_SERVICE_NAME and never touches Firebase
# Hosting, so claiming the live domain unconditionally would tell a preview
# deploy it had just published production.
if [ "$ONLY" = "hosting" ] || { [ "$ONLY" = "all" ] && [ "${SERVICE_NAME}" = "tracerlensai-app" ]; }; then
    echo "✅ Deployment finished. Live at https://tracerlensai.com"
elif [ "${SERVICE_NAME}" = "tracerlensai-app" ]; then
    echo "✅ Deployment finished. Service ${SERVICE_NAME} (${REGION}) — serving https://tracerlensai.com"
else
    SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
        --region "${REGION}" --project "${PROJECT_ID}" \
        --format='value(status.url)' 2>/dev/null || true)
    echo "✅ Deployment finished. Service ${SERVICE_NAME} (${REGION})${SERVICE_URL:+ — ${SERVICE_URL}}"
    echo "   Production (https://tracerlensai.com) was not touched."
fi
