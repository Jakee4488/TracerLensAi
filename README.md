# Enterprise Agentic Customer Support Orchestrator

Welcome to the **Enterprise Agentic Customer Support Orchestrator**, a cloud-native, production-ready AI orchestration platform built specifically for Google Cloud Platform (GCP).

This project provides a robust architecture for deploying LLM-powered customer support agents capable of tool usage, intent classification, multi-model fallback routing, and strict human-in-the-loop escalation policies.

## 🌟 Key Features

*   **Native GCP Architecture**: Designed from the ground up to leverage GCP managed services including GKE, Vertex AI, Artifact Registry, Cloud Storage, and BigQuery.
*   **Multi-Model Abstraction Layer**: An `ai_gateway` module that cleanly separates the LLM provider SDK from the core logic, featuring automatic fallback mechanisms (e.g., from Gemini 1.5 Flash to Pro) if rate limits or API errors occur.
*   **Agentic Graph Engine**: A customizable Python-based orchestrator that manages conversational state, parses intents, and dynamically invokes external mock tools (e.g., `fetch_user_profile`, `check_order_status`).
*   **Business Policy Enforcement**: Strict heuristic and ML-based routing policies that instantly escalate high-severity interactions to human agents.
*   **Enterprise Observability**: Integrated structured JSON logging for precise BigQuery ingestion, capturing token counts, latency, and costs per invocation loop.
*   **Keyless CI/CD & Security**: Implements GKE Workload Identity and GitHub Actions OIDC federation, entirely eliminating the need for long-lived service account JSON keys.

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
└── .github/workflows/           # CI/CD Pipeline Definitions
```

## 🚀 Getting Started

### Prerequisites

1.  **GCP Account**: An active GCP Project with billing enabled.
2.  **Tools**: Install `gcloud` CLI, `terraform`, `docker`, and `kubectl`.
3.  **Authentication**: Authenticate locally using Application Default Credentials (ADC):
    ```bash
    gcloud auth application-default login
    ```

### Local Development

1.  Navigate to the `src/` directory and install dependencies:
    ```bash
    pip install -r src/requirements.txt
    ```
2.  Set required environment variables:
    ```bash
    export GOOGLE_CLOUD_PROJECT="your-project-id"
    export GOOGLE_CLOUD_REGION="europe-west2"
    ```
3.  Run the FastAPI application locally:
    ```bash
    uvicorn src.main:app --reload --port 8080
    ```
4.  *(Optional)* Run the synthetic evaluation harness to test agent policies:
    ```bash
    python -m src.observability.evaluator
    ```

### Infrastructure Deployment

To deploy the cloud infrastructure, navigate to the `terraform/` directory:

1.  Initialize Terraform:
    ```bash
    terraform init
    ```
2.  Review the execution plan:
    ```bash
    terraform plan -var="project_id=your-project-id"
    ```
3.  Apply the configuration:
    ```bash
    terraform apply -var="project_id=your-project-id"
    ```

## 📚 Documentation

For an in-depth look at modifying the agent state graph, adding new LLM providers, and managing the Workload Identity CI/CD setup, please refer to the [Advanced Developer Guide](docs/developer_guide.md).
