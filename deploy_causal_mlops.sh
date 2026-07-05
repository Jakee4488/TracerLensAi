#!/bin/bash
set -euo pipefail

# =============================================================================
#  deploy_causal_mlops.sh
#  Deploys the complete Causal MLOps infrastructure and services.
# =============================================================================

echo -e "\n🚀 Starting Causal MLOps Deployment...\n"

# 1. Load config
if [ -f ".env" ]; then
  source .env
else
  echo "❌ Error: .env file not found. Please ensure it exists."
  exit 1
fi

PROJECT_ID=${GOOGLE_CLOUD_PROJECT}
REGION=${GOOGLE_CLOUD_REGION:-europe-west2}
AR_REPO="causal-mlops-repo"
AR_REGISTRY="${REGION}-docker.pkg.dev"

# 2. Terraform Provisioning
echo "🏗️  Applying Terraform Infrastructure..."
cd terraform
terraform init
terraform apply -auto-approve -var="project_id=${PROJECT_ID}" -var="region=${REGION}"
cd ..

# 3. Build & Push Docker Images
echo -e "\n🐳 Building and pushing Docker containers..."
gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin https://${AR_REGISTRY}

VERTEX_IMAGE="${AR_REGISTRY}/${PROJECT_ID}/${AR_REPO}/causal-components:latest"
API_IMAGE="${AR_REGISTRY}/${PROJECT_ID}/${AR_REPO}/calculator-api:latest"

echo "   -> Building Vertex AI Components..."
docker build -t ${VERTEX_IMAGE} src/causal_mlops/causal_engine/components/
docker push ${VERTEX_IMAGE}

echo "   -> Building Cloud Run Calculator API..."
docker build -t ${API_IMAGE} src/causal_mlops/calculator_api/
docker push ${API_IMAGE}

# 4. Deploy Cloud Function
echo -e "\n☁️  Deploying Data Ingestion Cloud Function..."
gcloud functions deploy causal-data-ingestion \
  --gen2 \
  --runtime python310 \
  --region ${REGION} \
  --source src/causal_mlops/data_ingestion \
  --entry-point ingest_synthetic_traces \
  --trigger-http \
  --allow-unauthenticated \
  --project ${PROJECT_ID}

# 5. Deploy Cloud Run Service
echo -e "\n🏃 Deploying Token Calculator API to Cloud Run..."
gcloud run deploy causal-calculator-api \
  --image ${API_IMAGE} \
  --region ${REGION} \
  --allow-unauthenticated \
  --set-env-vars="GCS_BUCKET_NAME=tracerlens-causal-artifacts-${PROJECT_ID}" \
  --project ${PROJECT_ID}

# 6. Retrieve API URL
API_URL=$(gcloud run services describe causal-calculator-api \
  --platform managed \
  --region ${REGION} \
  --project ${PROJECT_ID} \
  --format 'value(status.url)')

echo -e "\n✅ Deployment Complete!"
echo -e "   -> Calculator API is live at: ${API_URL}"
echo -e "\nDon't forget to update your .env with the new API URL:"
echo -e "CAUSAL_CALCULATOR_API_URL=${API_URL}"
