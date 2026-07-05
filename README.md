# TracerLensAi

Welcome to **TracerLensAi**, a cloud-native, production-ready AI orchestration platform built specifically for Google Cloud Platform (GCP).

This project provides a robust architecture for deploying LLM-powered customer support agents capable of tool usage, intent classification, multi-model fallback routing, and strict human-in-the-loop escalation policies.

## 🌟 Key Features

*   **Native GCP Architecture**: Designed from the ground up to leverage GCP managed services including GKE, Vertex AI, Artifact Registry, Cloud Storage, and BigQuery.
*   **Multi-Model Abstraction Layer**: An `ai_gateway` module that cleanly separates the LLM provider SDK from the core logic, featuring automatic fallback mechanisms (e.g., from Gemini 1.5 Flash to Pro) if rate limits or API errors occur.
*   **Agentic Graph Engine**: A customizable Python-based orchestrator that manages conversational state, parses intents, and dynamically invokes external mock tools (e.g., `fetch_user_profile`, `check_order_status`).
*   **Business Policy Enforcement**: Strict heuristic and ML-based routing policies that instantly escalate high-severity interactions to human agents.
*   **Enterprise Observability**: Integrated structured JSON logging for precise BigQuery ingestion, capturing token counts, latency, and costs per invocation loop.
*   **Token Compounding Engine**: Advanced mathematical projection of token costs across sequential loops. [See the Token Calculation Docs](docs/token_calculation.md).
*   **Keyless CI/CD & Security**: Implements GKE Workload Identity and GitHub Actions OIDC federation, entirely eliminating the need for long-lived service account JSON keys.
*   **Dockerized Local Dev**: Hot-reloadable local Docker Compose environment matching production execution characteristics.

---

## 📂 Repository Structure

```text
.
├── terraform/                   # GCP Infrastructure as Code (VPC, GKE, IAM, Storage)
├── src/                         # Core Python Application
│   ├── ai_gateway/              # LLM Abstraction & Fallback logic
│   ├── agent_engine/            # Orchestrator, Tools, and Routing Policies
│   ├── observability/           # Logging configuration and synthetic evaluation
│   └── main.py                  # FastAPI Entrypoint
├── deploy/                      # Containerization (Dockerfile) & Helm Charts
├── docs/                        # Advanced Developer Documentation
├── .github/workflows/           # CI/CD Pipeline Definitions (ci.yml, deploy.yml)
├── docker-compose.dev.yml       # Local development Docker Compose definition
├── run_tests.sh                 # Docker-based test, lint, and hot-reload runner
├── build_deploy.sh              # Emergency/manual GCP deploy fallback script
└── .env.example                 # Template for local dev environment configurations
```

---

## 🚀 Getting Started

### Prerequisites

1.  **Docker & Docker Compose**: Install Docker Desktop on your machine.
2.  **GCP Account**: An active GCP Project with Vertex AI APIs enabled.
3.  **Tools**: Install `gcloud` CLI, `terraform`, `kubectl`, and `helm` (only needed for infrastructure setup or manual deployments).

---

### Local Development & Testing

All local development and verification is done **inside Docker** using `run_tests.sh` to ensure exact parity with production.

1.  **Configure Environment**:
    Copy the template environment file to `.env`:
    ```bash
    cp .env.example .env
    ```
    Open `.env` and fill in your `GOOGLE_CLOUD_PROJECT` and credentials if needed.

2.  **Run the Test Pipeline**:
    Execute the test runner script to run the complete build, lint, pytest, and evaluator harness test suite inside containers:
    ```bash
    chmod +x run_tests.sh
    ./run_tests.sh
    ```
    This script will:
    *   Build the local development Docker image.
    *   Run `flake8` linting on `/src`.
    *   Run `pytest` unit tests.
    *   Run the Vertex AI synthetic evaluation harness.
    *   Start the containerized service and verify health endpoints.
    *   Clean up all temporary Docker resources automatically.

3.  **Run the Hot-Reload Dev Server**:
    To develop interactively with live code reload:
    ```bash
    ./run_tests.sh --start
    ```
    The application will start on `http://localhost:8080`. Any changes to files in `./src` will trigger an automatic application reload.

4.  **Stop the Dev Server**:
    When finished developing:
    ```bash
    ./run_tests.sh --stop
    ```

5.  **Clean Docker Resources**:
    To wipe all containers, local images, and compose volumes:
    ```bash
    ./run_tests.sh --clean
    ```

---

## 🚀 Deployment & CI/CD

Deployment is fully automated and orchestrated via GitHub Actions CI/CD workflows.

### 1. Automated (Primary) Deployments
*   **Lint & Test Gating**: Every Pull Request targeting `main` triggers `.github/workflows/ci.yml` to lint and test the code.
*   **Staging Deploy**: Merges into the `main` branch trigger `.github/workflows/deploy.yml` to build, tag (`latest` + Git SHA), push to GCP Artifact Registry, and deploy to the GKE Staging environment.
*   **Production Deploy**: Pushing a git release tag matching `v[0-9]+.*` (e.g., `v1.2.0`) triggers deployment to GKE Production (subject to manual approval gates in GitHub Environments).

### 2. Manual Deploy Fallback
If the CI/CD pipeline is unavailable, you can manually build, push, and deploy to GCP using `build_deploy.sh`. 

> [!WARNING]
> Manual deployment should only be used as a last resort/escape hatch. Always prefer standard branch merges for deployments.

*   **Dry Run (Verify actions first)**:
    ```bash
    chmod +x build_deploy.sh
    ./build_deploy.sh --dry-run
    ```
*   **Execute Deployment**:
    ```bash
    ./build_deploy.sh
    ```
    *(Requires local `gcloud` authentication and container cluster access).*

---

## 📚 Documentation

For an in-depth look at modifying the agent state graph, adding new LLM providers, local Docker architecture, and managing the Workload Identity CI/CD setup, please refer to the [Advanced Developer Guide](docs/developer_guide.md).
