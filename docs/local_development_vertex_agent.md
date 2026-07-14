# Local Development with Vertex AI Agent

This guide outlines the steps required to run the TracerLensAi stack locally (UI + Agent Backend) while authenticating securely with Google Cloud Vertex AI via Application Default Credentials (ADC).

## 1. Configure GCP Authentication (ADC)
Since we are using Vertex AI instead of the consumer Gemini API, you need to ensure your local environment is authenticated with Google Cloud.

Run the following command in your WSL/Linux terminal and follow the browser prompts:
```bash
gcloud auth application-default login
```
*This generates a credential file locally, typically located at `~/.config/gcloud/application_default_credentials.json`.*

## 2. Environment Setup (`.env`)
Ensure your `.env` file at the root of the project contains the following variables to force the Google GenAI SDK to use Vertex AI:

```env
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_GENAI_USE_VERTEXAI=true
```
*(Remove or comment out `GEMINI_API_KEY` to avoid conflicts).*

## 3. Run the Backend Agent (Docker Compose)
We use Docker Compose to run the FastAPI backend on port `8080`. 

The `docker-compose.dev.yml` file is specifically configured for local Vertex AI development:
- It maps your local ADC credential file into the container (`/tmp/adc.json`).
- It runs as `root` to avoid host-level permission errors when reading the credentials.
- It sets `PYTHONPATH` to ensure Python finds the dependencies installed by the non-root builder user.
- It maps the `./src` directory for instant hot-reloading.

Run the backend:
```bash
docker compose -f docker-compose.dev.yml up --build
```
*(Keep this terminal open).*

## 4. Run the Proxy / UI Frontend
The proxy server serves the frontend UI and routes API calls to the backend. Because we are testing locally, we need to manually point the proxy to our running Docker container.

Open a **new terminal tab** and run:

```bash
# Point the proxy to the local backend's streaming endpoint
export AGENT_ENGINE_ENDPOINT="http://127.0.0.1:8080/api/stream_reasoning_engine"

# Start the proxy server
uvicorn proxy.main:app --host 0.0.0.0 --port 8001 --reload
```

## 5. Access the Application
With both the Backend (Docker) and the Frontend (Uvicorn Proxy) running, you can now access the full application in your browser:

**[http://localhost:8001/static/index.html](http://localhost:8001/static/index.html)**

### Troubleshooting Notes
- **Empty Response / `b''`**: If the backend crashes mid-stream (e.g., SessionNotFoundError), the UI will show an empty response. Check the Docker compose logs.
- **Port 8001 in use**: If you get `[Errno 98] Address already in use`, find the zombie process using `fuser -k 8001/tcp`.
- **404 Not Found**: Ensure `AGENT_ENGINE_ENDPOINT` exactly matches the `/api/stream_reasoning_engine` path with no trailing colons.

## 6. Exercising the Causal Reasoning Pathway

With both processes running, flip the **Causal Reasoning** toggle in the header and send a prompt, or hit the proxy directly:

```bash
curl -s localhost:8001/analyze-prompt \
  -H 'content-type: application/json' \
  -d '{"prompt": "If I raise prices 10%, what happens to revenue given elastic demand? Compute scenarios.", "causal_reasoning": true}' \
  | python -m json.tool
```

Expect `causal_reasoning_steps` (the plan/replan trace), `causal_graph` (`nodes`/`edges`/`critical_path`, rendered as a Mermaid diagram in the UI), `causal_status`, and a `response` containing only the synthesizer's final answer. Without `AGENT_ENGINE_ENDPOINT` set, the proxy's mock path returns a canned 3-node graph so the UI panel is developable offline.
