# Advanced Developer Guide

This document provides deep technical context on the architecture and extension points of the Enterprise Agentic Customer Support Orchestrator.

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

## 2. Agent Engine & Orchestration (`src/agent_engine`)

The orchestration logic is intentionally kept lightweight (avoiding heavy, opaque frameworks) to ensure maximum observability and control over the interaction graph.

### Modifying the State Loop (`orchestrator.py`)

The `process_inquiry` method handles the core conversational loop:
1.  **Generation**: LLM processes the user prompt.
2.  **Tool Execution**: The code currently mocks tool execution for brevity. To implement real tool execution loops (like ReAct or LangGraph paradigms), you should:
    *   Parse the `response.tool_calls` array.
    *   Map the requested tool to `TOOL_REGISTRY`.
    *   Execute the tool.
    *   Append the result to the session context.
    *   Trigger a recursive or subsequent LLM generation pass with the updated context.
3.  **Policy Enforcement**: Before returning a response to the user, the text is passed through the `RoutingPolicy`.

### Adding New Tools (`tools.py`)

To add a new capability to the agent:
1.  Define an asynchronous Python function in `tools.py`. Ensure it includes a descriptive docstring and type hints (these are used by the LLM to understand how to use the tool).
2.  Register the function in the `TOOL_REGISTRY` dictionary.

---

## 3. Security & CI/CD (Workload Identity)

The system relies heavily on **Google Cloud Workload Identity**.

### GKE Runtime Security
In `terraform/iam.tf`, the GCP Service Account (`agent-app-sa`) is bound to the Kubernetes Service Account (`agent-ksa`). 
The `helm/values.yaml` annotates the Kubernetes Service Account. When the application pods run, the Google Cloud client libraries automatically detect the Workload Identity endpoint and authenticate *keylessly*. 

**Important:** Never inject `GOOGLE_APPLICATION_CREDENTIALS` JSON keys into the GKE cluster.

### GitHub Actions OIDC Federation
The `.github/workflows/deploy.yml` pipeline authenticates to GCP using OpenID Connect (OIDC).
1.  GitHub Actions requests a short-lived OIDC token.
2.  The `google-github-actions/auth` step exchanges this token with the Workload Identity Pool created in `terraform/iam.tf`.
3.  GCP verifies the token signature and the repository claim (ensuring only your specific repo can assume the role).
4.  The pipeline assumes the `github-actions-sa` Service Account to execute `docker push` and `terraform apply`.

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
