#!/bin/bash
set -euo pipefail

TARGET="cloudrun"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --target) TARGET="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ -f ".env" ]; then
    source .env
else
    echo "Error: .env file missing. Needed for GOOGLE_CLOUD_PROJECT."
    exit 1
fi

PROJECT_ID=${GOOGLE_CLOUD_PROJECT}
REGION=${GOOGLE_CLOUD_REGION:-us-central1}
IMAGE_NAME="gcr.io/${PROJECT_ID}/tracerlensai-app:latest"

if [ "$TARGET" = "cloudrun" ] || [ "$TARGET" = "gke" ]; then
    echo "Building and pushing image to Artifact Registry..."
    gcloud auth configure-docker gcr.io --quiet
    docker build -t ${IMAGE_NAME} .
    docker push ${IMAGE_NAME}
fi

if [ "$TARGET" = "cloudrun" ]; then
    echo "Deploying to Cloud Run..."
    gcloud run deploy tracerlensai-app \
        --image ${IMAGE_NAME} \
        --region ${REGION} \
        --project ${PROJECT_ID} \
        --allow-unauthenticated
elif [ "$TARGET" = "cloudrun-functions" ]; then
    echo "Deploying to Cloud Run Functions..."
    gcloud functions deploy tracerlensai-app \
        --gen2 \
        --runtime=python312 \
        --region=${REGION} \
        --source=. \
        --entry-point=proxy_app \
        --trigger-http \
        --allow-unauthenticated \
        --project=${PROJECT_ID}
elif [ "$TARGET" = "gke" ]; then
    echo "Deploying to GKE..."
    gcloud container clusters get-credentials my-cluster --region ${REGION} --project ${PROJECT_ID}
    helm upgrade --install tracerlensai ./helm/tracerlensai \
        --set image.tag=latest \
        --set env.GOOGLE_CLOUD_PROJECT=${PROJECT_ID} \
        --set env.GOOGLE_CLOUD_REGION=${REGION}
elif [ "$TARGET" = "agent-runtime" ]; then
    echo "Evaluating and Deploying to Gemini Enterprise Agent Platform..."
    # Evaluate the agent before deploying
    agents-cli eval
    agents-cli deploy --project ${PROJECT_ID} --region ${REGION} --deployment-target agent_runtime
else
    echo "Unknown target: $TARGET"
    exit 1
fi

echo "Deployment finished."
