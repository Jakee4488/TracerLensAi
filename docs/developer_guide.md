# Advanced Developer Guide

This document provides deep technical context on the architecture, local container setup, extension points, and deployment paradigms of the Enterprise Agentic Customer Support Orchestrator.

---

## 1. Multi-Provider AI Gateway (`src/ai_gateway`)

The AI Gateway is designed to abstract away the underlying LLM provider SDKs (e.g., Vertex AI, OpenAI, Anthropic). This is critical for enterprise architectures to avoid vendor lock-in and handle region-specific outages.

### Adding a New Provider

To add a new provider (e.g., Anthropic Claude natively or via Vertex):

1.  **Implement the Client**: Create a new file (e.g., `claude_client.py`). Ensure it implements an `async def generate_response()` method that returns the standardized `AgentResponse` Pydantic model defined in `interface.py`.
2.  **Handle Asynchrony**: Ensure that synchronous SDK calls are wrapped in `asyncio.to_thread()` to prevent blocking the FastAPI event loop, as demonstrated in `vertex_client.py`.
3.  **Update the Fallback Manager**: Modify `fallback_manager.py` to instantiate the new client and integrate its models into the `fallback_sequence` array.

### Fallback Strategy

The `FallbackManager` iterates through an array of models. If `generate_response()` returns an `AgentResponse` with a populated `.error` attribute (due to a 429 Too Many Requests, 500 Internal Server Error, etc.), the manager logs the error and gracefully falls back to the next model in the chain.

---

## 2. Local Container Architecture & Developer Loop

To guarantee that the local development environment mirrors GKE staging and production behavior, all developer workflows run inside Docker via **Docker Compose**.

### Compose Configuration (`docker-compose.dev.yml`)

The compose environment contains two services built using `deploy/Dockerfile`:

- **`agent-app`**: The application server.
  - **Hot-Reload**: Mounts `./src` as a read-only volume to `/app/src`.
  - **Dev Command**: Overrides the default container startup with `uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload` to detect changes on the host and reload immediately.
  - **Ports**: Maps container port `8080` to host port `8080`.
- **`test-runner`**: A profile-scoped container used for one-shot test operations.
  - Mounts `./src`, `./tests`, and `pytest.ini`.
  - Allows running test suites, linting checks, and evaluation scripts on demand without affecting the active application server container.

### Developer Script Wrapper (`run_tests.sh`)

Instead of invoking `docker compose` commands directly, use `./run_tests.sh` to automate local tasks:

- **Running full test pipelines**: `./run_tests.sh` builds the dev image, executes `flake8 src/`, runs `pytest tests/`, executes `python -m src.observability.evaluator`, runs a health-check smoke test against a spun-up server, and cleans up containers.
- **Interactive work**: Run `./run_tests.sh --start` to start the app in the background and track file changes. Run `./run_tests.sh --stop` to tear it down.

---

## 3. GKE Deployments & CI/CD Pipelines

Deployment is governed by a **GitOps pipeline** orchestrated via GitHub Actions.

```
                  ┌──────────────────────────────────────────────┐
                  │              Merge into main                 │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │          CI: Runs tests & linter             │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │      CD: Builds image, pushes to AR          │
                  │      & deploys to GKE Staging (Helm)         │
                  └──────────────────────┬───────────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
            [Create release tag vX.Y.Z]         [Manual Fallback Path]
                         │                               │
                         ▼                               ▼
            ┌───────────────────────────┐    ┌───────────────────────┐
            │ Production Deploy (CD)    │    │   build_deploy.sh     │
            │ Requires approval gate    │    │  gcloud auth-required │
            └───────────────────────────┘    └───────────────────────┘
```

### Security & Authentication

- **GKE Runtime Security**: We map Kubernetes Service Accounts (KSA) to GCP Service Accounts (GSA) via **Workload Identity**. The pod specification annotates the `agent-ksa` service account. GCP SDK clients auto-authenticate keylessly at runtime. Long-lived key files are completely blocked.
- **CI/CD Pipeline Security**: GitHub Actions uses Workload Identity Federation (OIDC) to authenticate to Google Cloud. The pool validates repository claims before assuming the `github-actions-sa` identity.

### Manual Fallback Deployment (`build_deploy.sh`)

In emergency circumstances (e.g., GitHub Actions outage), you can deploy manually using `build_deploy.sh`.

- Verify your changes dry-run first: `./build_deploy.sh --dry-run`
- Execute the deploy (you will be prompted to confirm): `./build_deploy.sh`
- The script logs outputs to `logs/build_deploy_YYYYMMDD_HHMMSS.log`

---

## 4. Observability Data Sink

The `logger.py` module detects if the application is running inside GKE. If so, it leverages `google.cloud.logging` to write structured JSON payloads.

### BigQuery Integration

In the GCP Console, you can create a **Log Router Sink** that filters for the `"agent_invocation"` event type and streams these logs directly into the BigQuery dataset provisioned by `terraform/storage.tf`.

This enables complex SQL analytics on agent performance:

```sql
-- Example BigQuery Query: Average Latency per Model
SELECT
    jsonPayload.model,
    AVG(CAST(jsonPayload.latency_ms AS FLOAT64)) as avg_latency
FROM `your_project.agent_orchestrator_logs.stdout`
GROUP BY jsonPayload.model
```

### Token Compounding Engine

The platform features an advanced observability engine that predicts token usage costs based on sequential agent loop histories. Because context size increases sequentially in multi-turn architectures, we use an arithmetic progression formula (`N(N−1)/2`) to compute compounding costs.

For a detailed explanation of the math, projection metrics, and risk analysis, please refer to the [Token Calculation and Compounding Analysis](token_calculation.md) guide.
