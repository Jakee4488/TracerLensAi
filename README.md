# TracerLensAi

🌐 **[Live Application](https://tracerlensai.com/)** · ![Build](https://github.com/Jakee4488/TracerLensAi/actions/workflows/deploy.yml/badge.svg)

**TracerLensAi** is a cloud-native AI chat interface built on the **Gemini Enterprise Agent Platform**. It provides an interactive workspace for evaluating agentic workflows with causal reasoning, live code execution, and web search — all backed by a production-grade GCP deployment pipeline utilizing Agent Runtime and a lightweight Cloud Run proxy.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Gemini Enterprise Agent** | Built using the Agent Development Kit (ADK) and deployed to Vertex AI Agent Runtime. |
| **Code Execution** | Gemini can write and run Python code safely within the Agent Sandbox. |
| **Web Search** | Toggle Google Search grounding to give Gemini real-time internet access. |
| **Memory Bank** | Full conversation history automatically stored and persisted across sessions by the Agent Engine. |
| **Lightweight Proxy** | Secure FastAPI proxy deployed on Cloud Run to protect Agent API keys from the client. |
| **Keyless CI/CD** | GitHub Actions deploys the ADK Agent and Proxy using Workload Identity Federation — zero long-lived keys. |
| **Firebase Hosting** | Static frontend served globally via CDN with automatic SSL and custom domain (`tracerlensai.com`). |

---

## 🔄 How It Works

```
┌─────────────┐      POST /analyze-prompt      ┌──────────────────────┐
│             │ ──────────────────────────────▶│                      │
│ User        │                                │ Firebase             │
│ Browser     │ ◀───────────────────────────── │ Hosting (CDN)        │
│             │    Rendered Markdown + Code    │                      │
└─────────────┘                                └───────────┬──────────┘
                                                           │ Rewrite Proxy
                                                           ▼
                                               ┌──────────────────────┐
                                               │                      │
                                               │ FastAPI Proxy        │
                                               │ (Cloud Run)          │
                                               │                      │
                                               └───┬──────────────┬───┘
                                                   │              │
                                          Forward  │              │ Agent SDK
                                          Request  │              │
                                                   ▼              ▼
                                              ┌────────────────────────┐
                                              │ Vertex AI Agent Engine │
                                              │ (ADK Agent Runtime)    │
                                              │                        │
                                              │ ┌────────────────────┐ │
                                              │ │    Memory Bank     │ │
                                              │ └────────────────────┘ │
                                              └────────────────────────┘
```

**Flow:** User sends a prompt → Firebase Hosting serves the static UI and proxies the API call to Cloud Run → The FastAPI Proxy forwards the request with a Session ID to the Vertex AI Agent Engine → The Agent retrieves history from the Memory Bank, uses tools, and generates a response → The Agent Engine persists the new context and returns the result → The frontend parses Markdown and updates.

---

## 🚀 Deployment & CI/CD

### Automated Deployment (Primary)

Authentication is fully keyless via **Workload Identity Federation** (OIDC). The `.github/workflows/deploy.yml` pipeline handles:
1. `agents-cli eval` to validate the ADK Agent.
2. `agents-cli deploy` to package and push the Agent to the Agent Registry (Vertex AI).
3. Cloud Run deployment for the lightweight proxy.

### Manual Fallback Script

If CI/CD is unavailable:
```bash
# Deploy the Proxy to Cloud Run
./deploy_to_gcp.sh --target cloudrun

# Deploy the Agent to Agent Runtime
./deploy_to_gcp.sh --target agent-runtime
```

---

## 📚 Documentation

| Document | Description |
|---|---|
| [Developer Guide](docs/developer_guide.md) | Architecture deep-dive, full API reference, function-level docs for every source file |
| [Token Calculation](docs/token_calculation.md) | How the Memory Bank and agentic loops impact token compounding and costs |
| [Deployment Guide](docs/deployment_guide.md) | Step-by-step deployment methods and infrastructure provisioning |
