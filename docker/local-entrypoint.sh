#!/bin/sh
# Local-dev-only entrypoint for the proxy container (docker-compose.yml).
#
# Not used by Dockerfile.proxy's own CMD / the deployed Cloud Run image — this
# is bind-mounted in only for `docker compose up`, so it never touches
# production. It resolves AGENT_ENGINE_ENDPOINT the same way deploy_to_gcp.sh
# does: from deployment_metadata.json, which agents-cli keeps current on every
# agent deploy. That makes this compose file self-updating — redeploy the
# agent, and the next `docker compose up` picks up the new engine automatically.
set -e

if [ "$MODE" = "mock" ]; then
    echo "[local-entrypoint] MODE=mock — AGENT_ENGINE_ENDPOINT left unset; proxy serves its built-in canned responses."
    unset AGENT_ENGINE_ENDPOINT
elif [ -z "$AGENT_ENGINE_ENDPOINT" ] && [ -f /app/deployment_metadata.json ]; then
    ENGINE_ID=$(sed -n 's/.*"remote_agent_runtime_id": *"\([^"]*\)".*/\1/p' /app/deployment_metadata.json)
    if [ -n "$ENGINE_ID" ]; then
        export AGENT_ENGINE_ENDPOINT="https://${GOOGLE_CLOUD_REGION:-europe-west2}-aiplatform.googleapis.com/v1beta1/${ENGINE_ID}:query"
        echo "[local-entrypoint] Resolved from deployment_metadata.json -> AGENT_ENGINE_ENDPOINT=$AGENT_ENGINE_ENDPOINT"
    else
        echo "[local-entrypoint] deployment_metadata.json present but remote_agent_runtime_id not found; falling back to mock mode."
    fi
else
    echo "[local-entrypoint] Using AGENT_ENGINE_ENDPOINT from the environment: $AGENT_ENGINE_ENDPOINT"
fi

exec uvicorn proxy.main:app --host 0.0.0.0 --port 8080
