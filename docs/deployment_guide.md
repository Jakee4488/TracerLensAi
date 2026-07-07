# TracerLensAi Deployment Guide

This guide details the deployment architecture and the step-by-step procedures for deploying TracerLensAi to Google Cloud Platform (GCP).

## Architecture Overview

TracerLensAi is a containerized FastAPI application. It is configured to deploy to either:
1. **Google Cloud Run (Recommended):** Serverless, scales to zero, ideal for stateless web applications.
2. **Google Kubernetes Engine (GKE):** Orchestrated deployment using Helm, suitable if you are integrating into a larger microservices ecosystem.

## Deployment Workflows

We have three primary methods for deploying the application, ordered by preference.

### Method 1: Continuous Deployment via GitHub Actions (Primary)

The CI/CD pipeline is fully automated through GitHub Actions.

**Trigger:** Merging or pushing a commit to the `main` branch.
**Workflow (`.github/workflows/cd.yml`):**
1. Authenticates securely to Google Cloud using Workload Identity Federation or a Service Account Key.
2. Builds the Docker image.
3. Tags and pushes the image to Google Artifact Registry.
4. Reads the `DEPLOY_TARGET` environment variable/input.
5. Deploys the new image to either Cloud Run or GKE dynamically.

**Manual Trigger:** You can manually trigger the deployment from the GitHub Actions tab, where you will be prompted to select your `DEPLOY_TARGET` (`cloudrun` or `gke`).

### Method 2: Local Fallback Script (Emergency / Direct)

If CI/CD is down or you need to test a deployment from your local machine, a fallback bash script is provided in the project root.

**Prerequisites:**
- You must have the Google Cloud SDK (`gcloud`) installed and authenticated locally.
- You must have a `.env` file at the root containing `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_REGION`.

**Command:**
```bash
# Deploy to Cloud Run
./deploy_to_gcp.sh --target cloudrun

# Deploy to GKE
./deploy_to_gcp.sh --target gke
```

**What it does:** The script builds the image using local Docker, pushes it to your Artifact Registry using `gcloud auth configure-docker`, and issues the deployment command directly.

### Method 3: Local Dev & Commit Guard

For safe local development, use the `run_tests.sh` script to ensure you only push passing code.

**Command:**
```bash
./run_tests.sh --commit "Your descriptive commit message"
```

**What it does:**
1. Runs `flake8` linting.
2. Runs `pytest` unit tests (including in-memory DB mocks).
3. Evaluates the UI and Smoke tests.
4. If everything passes, it displays the `git diff` and commits the code.
5. Prompts you to push to remote, which then triggers the GitHub Actions CD (Method 1).

## Infrastructure Provisioning

If this is a brand new environment, the infrastructure must be provisioned before the application can deploy.

### Terraform (Cloud Run & Resources)
1. Navigate to the `terraform/` directory.
2. Ensure you have authenticated to GCP (`gcloud auth application-default login`).
3. Run `terraform init`.
4. Run `terraform apply` to provision the Cloud Run service, IAM permissions, and any necessary storage/databases.

### Helm (GKE Alternative)
If you opted for GKE, the Kubernetes manifests are templatized using Helm in the `helm/tracerlensai` directory.
- Values like resources, replicas, and environment variables can be tweaked in `helm/tracerlensai/values.yaml`.
- The `deploy_to_gcp.sh` script automatically handles the `helm upgrade --install` command.
